#!/usr/bin/env bash
# Execute dentro do Terminal/SSH do HAOS. Não instala Docker nem altera o pipeline.
set -Eeuo pipefail
action=${1:---check}
case "$action" in --check|--install) ;; *) printf 'Uso: bash install-haos.sh [--check|--install]\n' >&2; exit 1 ;; esac
command -v ha >/dev/null || { printf 'ERRO: CLI ha ausente; execute no Terminal/SSH do HAOS.\n' >&2; exit 1; }
command -v jq >/dev/null || { printf 'ERRO: jq ausente.\n' >&2; exit 1; }
# Alias addons funciona também no CLI anterior à renomeação para apps.
apps_command=apps
if ! ha apps --help >/dev/null 2>&1; then apps_command=addons; fi
ha os info >/dev/null
inventory=$(ha "$apps_command" --raw-json)
installed=$(jq -r '(.data.addons // .addons) as $apps | if ($apps | type) == "array" then ($apps | any(.slug == "core_openwakeword")) else error("Inventário HA inesperado") end' <<<"$inventory")
printf 'HAOS acessível; openWakeWord instalado: %s\n' "$installed"
if [[ $action == --check ]]; then
    printf 'Somente leitura. --install cria backup completo antes de instalar/iniciar.\n'
    exit 0
fi
ha backups new --name "Antes do Arandu Wake $(date -u +%Y%m%dT%H%M%SZ)"
if [[ $installed != true ]]; then ha "$apps_command" install core_openwakeword; fi
info=$(ha "$apps_command" info core_openwakeword --raw-json)
state=$(jq -r '.data.state // .state // "unknown"' <<<"$info")
if [[ $state != started ]]; then ha "$apps_command" start core_openwakeword; fi
for attempt in 1 2 3 4 5; do
    info=$(ha "$apps_command" info core_openwakeword --raw-json)
    state=$(jq -r '.data.state // .state // "unknown"' <<<"$info")
    if [[ $state == started ]]; then break; fi
    if [[ $attempt != 5 ]]; then sleep 1; fi
done
if [[ $state != started ]]; then
    printf 'ERRO: openWakeWord não confirmou estado started. Consulte os logs do aplicativo; pipeline não alterado.\n' >&2
    exit 1
fi
version=$(jq -r '.data.version // .version // "unknown"' <<<"$info")
printf 'Versão efetiva do aplicativo openWakeWord: %s (store HA; não fixada por este script).\n' "$version"
printf '%s\n' 'Aplicativo disponível. No painel: ative Iniciar ao ligar/Watchdog; aplique homeassistant/openwakeword-options.yaml e selecione Alexa no pipeline Arandu. Verifique a integração Wyoming descoberta. O pipeline existente não foi alterado.'
