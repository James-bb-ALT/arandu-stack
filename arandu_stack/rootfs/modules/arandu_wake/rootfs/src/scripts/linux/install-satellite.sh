#!/usr/bin/env bash
# Legacy, central-detection Wyoming satellite. Never run on HAOS or Android.
set -Eeuo pipefail
readonly COMMIT=7a3ba2bc23fc6699adae4f5bbac4ce529fe2a623
readonly REPO=https://github.com/rhasspy/wyoming-satellite.git
readonly ROOT=/opt/arandu-wake-satellite
readonly RELEASE="$ROOT/releases/$COMMIT"
readonly CONFIG=/etc/arandu-wake
readonly UNIT=/etc/systemd/system/arandu-wake-satellite.service
action=check
legacy=false
allow_lan=false
bind_ip=127.0.0.1
name=arandu-linux
mic=default
snd=default
fail() { printf 'ERRO: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'HELP'
Uso: bash install-satellite.sh [--check | --install --allow-legacy]
  --bind-ip IPv4     127.0.0.1 por padrão; IP LAN requer --allow-lan
  --allow-lan        Aceita porta sem TLS/auth; restrinja firewall ao HA
  --name NOME        Identificador simples do satélite
  --mic-device ALSA  Dispositivo de arecord -L (padrão: default)
  --snd-device ALSA  Dispositivo de aplay -L (padrão: default)
  --help            Mostra esta ajuda sem instalar
Requer Debian/Ubuntu, Linux amd64/arm64, Python 3.9–3.12, ALSA e systemd.
Wyoming Satellite está arquivado. Para novos equipamentos, avalie
https://github.com/OHF-Voice/linux-voice-assistant
HELP
}
while (($#)); do
  case "$1" in
    --check) action=check; shift ;;
    --install) action=install; shift ;;
    --allow-legacy) legacy=true; shift ;;
    --allow-lan) allow_lan=true; shift ;;
    --bind-ip|--name|--mic-device|--snd-device)
      (($# >= 2)) || fail "Falta valor para $1"
      case "$1" in
        --bind-ip) bind_ip=$2 ;;
        --name) name=$2 ;;
        --mic-device) mic=$2 ;;
        --snd-device) snd=$2 ;;
      esac
      shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) fail "Opção desconhecida: $1" ;;
  esac
done
[[ $name =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]] || fail 'Nome inválido'
for device in "$mic" "$snd"; do
  [[ $device =~ ^[A-Za-z0-9_:=,.-]+$ ]] || fail 'Identificador ALSA inválido'
done
[[ $(uname -s) == Linux ]] || fail 'Execute no satélite Linux, não em Windows/Android/HAOS'
case "$(uname -m)" in x86_64|aarch64) ;; *) fail 'Arquitetura suportada: amd64/arm64' ;; esac
[[ -r /etc/os-release ]] || fail '/etc/os-release ausente'
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}" in debian|ubuntu) ;; *) fail 'Instalador validado estaticamente para Debian/Ubuntu' ;; esac
[[ -d /run/systemd/system ]] || fail 'systemd não é o init ativo'
for tool in python3 git arecord aplay systemctl systemd-analyze ss getent install mktemp awk grep; do
  command -v "$tool" >/dev/null || fail "Falta $tool. Instale: git python3 python3-venv python3-pip alsa-utils iproute2"
done
python3 - "$bind_ip" "$allow_lan" <<'PY'
import ipaddress, sys, venv
if not (3, 9) <= sys.version_info[:2] < (3, 13):
    raise SystemExit('Python suportado: 3.9–3.12')
try:
    address = ipaddress.IPv4Address(sys.argv[1])
except ipaddress.AddressValueError:
    raise SystemExit('Use IPv4 literal, nunca hostname ou wildcard')
networks = [ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
if address != ipaddress.ip_address('127.0.0.1'):
    if not any(address in n for n in networks):
        raise SystemExit('Bind deve ser 127.0.0.1 ou endereço RFC1918 do satélite')
    if sys.argv[2] != 'true':
        raise SystemExit('Bind LAN requer --allow-lan; configure firewall restrito ao IP do HA')
PY
getent group audio >/dev/null || fail 'Grupo audio ausente'
printf 'Pré-requisitos OK. Origem fixa: %s @ %s\n' "$REPO" "$COMMIT"
printf 'Áudio: mic=%s, saída=%s. Endpoint: %s:10700\n' "$mic" "$snd" "$bind_ip"
if [[ $action == check ]]; then
  printf 'Somente pré-requisitos. Microfone, som e Home Assistant ainda não testados.\n'
  exit 0
fi
[[ $legacy == true ]] || fail 'Projeto arquivado: instalação requer --allow-legacy'
[[ $EUID != 0 ]] || fail 'Execute como usuário normal com sudo, não como root'
command -v sudo >/dev/null || fail 'sudo ausente'
user=$(id -un)
[[ $user =~ ^[a-z_][a-z0-9_-]*\$?$ ]] || fail 'Nome de usuário não suportado'
group=$(id -gn)
[[ $group =~ ^[a-z_][a-z0-9_-]*\$?$ ]] || fail 'Nome de grupo não suportado'
sudo -v
if ss -H -ltn 'sport = :10700' | grep -q . && \
   ! sudo systemctl is-active --quiet arandu-wake-satellite.service; then
  fail 'Porta TCP 10700 já está ocupada por outro serviço; não será alterado'
fi
work=$(mktemp -d)
cleanup() { [[ -n ${work:-} && -d $work ]] && rm -rf -- "$work"; }
trap cleanup EXIT
if [[ -e $ROOT && ! -f $ROOT/.arandu-managed ]]; then
  fail "$ROOT já existe sem marcador de propriedade; não será alterado"
fi
sudo install -d -m 0755 "$ROOT" "$ROOT/releases"
sudo touch "$ROOT/.arandu-managed"
if [[ ! -f $RELEASE/.ready ]]; then
  [[ ! -e $RELEASE ]] || fail "Instalação incompleta em $RELEASE; inspecione antes de tentar novamente"
  sudo install -d -m 0755 -o "$user" -g "$group" "$RELEASE"
  git -C "$RELEASE" init --quiet
  git -C "$RELEASE" remote add origin "$REPO"
  git -C "$RELEASE" fetch --depth 1 origin "$COMMIT"
  git -C "$RELEASE" checkout --detach --quiet FETCH_HEAD
  [[ $(git -C "$RELEASE" rev-parse HEAD) == "$COMMIT" ]] || fail 'Commit inesperado'
  python3 -m venv "$RELEASE/.venv"
  "$RELEASE/.venv/bin/python" -m pip install 'pip==25.0.1' 'setuptools==75.8.0' 'wheel==0.45.1'
  "$RELEASE/.venv/bin/python" -m pip install \
    'wyoming==1.5.4' 'zeroconf==0.88.0' 'pyring-buffer==1.1.0' \
    'ifaddr==0.2.0' 'async-timeout==5.0.1'
  "$RELEASE/.venv/bin/python" -m pip install --no-deps --no-build-isolation "$RELEASE"
  "$RELEASE/.venv/bin/python" -m pip check
  "$RELEASE/.venv/bin/python" -m wyoming_satellite --help >/dev/null
  touch "$RELEASE/.ready"
  sudo chown -R root:root "$RELEASE"
fi
# Arguments are JSON data; never concatenate user input into executable shell.
python3 - "$bind_ip" "$name" "$mic" "$snd" "$work/config.json" <<'PY'
import json, sys
with open(sys.argv[5], 'w', encoding='utf-8') as out:
    json.dump(dict(bind_ip=sys.argv[1], name=sys.argv[2], mic=sys.argv[3], snd=sys.argv[4]), out)
PY
cat >"$work/run.py" <<'PY'
import json, os, sys
with open('/etc/arandu-wake/satellite.json', encoding='utf-8') as source:
    cfg = json.load(source)
args = [sys.executable, '-m', 'wyoming_satellite',
        '--name', cfg['name'], '--uri', f"tcp://{cfg['bind_ip']}:10700",
        '--no-zeroconf',
        '--mic-command', f"arecord -D {cfg['mic']} -r 16000 -c 1 -f S16_LE -t raw",
        '--snd-command', f"aplay -D {cfg['snd']} -r 22050 -c 1 -f S16_LE -t raw"]
os.execv(sys.executable, args)
PY
cat >"$work/arandu-wake-satellite.service" <<UNITFILE
[Unit]
Description=Arandu Wake legacy Wyoming satellite (central Alexa)
Wants=network-online.target
After=network-online.target sound.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$user
Group=$group
SupplementaryGroups=audio
WorkingDirectory=$RELEASE
ExecStart=$RELEASE/.venv/bin/python $CONFIG/satellite-run.py
Restart=always
RestartSec=5
TimeoutStopSec=15
KillMode=control-group
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true
UMask=0077
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1

[Install]
WantedBy=multi-user.target
UNITFILE
python3 -m py_compile "$work/run.py"
sudo systemd-analyze verify "$work/arandu-wake-satellite.service"
stamp=$(date -u +%Y%m%dT%H%M%S)-$$
sudo install -d -m 0755 "$CONFIG"
for target in "$CONFIG/satellite.json" "$CONFIG/satellite-run.py" "$UNIT"; do
  if sudo test -f "$target"; then
    sudo cp -a -- "$target" "$target.backup-$stamp"
  fi
done
sudo install -m 0644 "$work/config.json" "$CONFIG/satellite.json"
sudo install -m 0644 "$work/run.py" "$CONFIG/satellite-run.py"
sudo install -m 0644 "$work/arandu-wake-satellite.service" "$UNIT"
sudo systemctl daemon-reload
sudo systemctl enable arandu-wake-satellite.service
# 'enable --now' does not restart an already running service after reconfigure.
sudo systemctl restart arandu-wake-satellite.service
active=false
for ((attempt = 0; attempt < 10; attempt++)); do
  if sudo systemctl is-active --quiet arandu-wake-satellite.service && \
     ss -H -ltn "sport = :10700" | awk '{print $4}' | grep -Fxq "$bind_ip:10700"; then
    active=true
    break
  fi
  sleep 1
done
if [[ $active != true ]]; then
  printf 'Serviço/porta não confirmado. Consulte journalctl -u arandu-wake-satellite -b.\n' >&2
  fail "Configuração anterior preservada como .backup-$stamp; consulte rollback"
fi
printf 'Processo ativo e porta TCP aberta. Isso não comprova áudio nem sessão Assist.\n'
printf 'Selecione no HA: satélite Wyoming → pipeline Arandu → wake word Alexa.\n'
if [[ $bind_ip == 127.0.0.1 ]]; then
  printf 'Loopback: HA remoto não alcança a porta. Para LAN, reconfigure explicitamente.\n'
else
  printf 'LAN sem autenticação/TLS: limite TCP 10700 ao IP do HA no firewall.\n'
fi
