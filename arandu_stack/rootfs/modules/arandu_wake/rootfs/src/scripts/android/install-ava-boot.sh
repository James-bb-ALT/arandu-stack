#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
umask 077
if [[ "${PREFIX:-}" != /data/data/com.termux/files/usr ]]; then
    printf 'Execute dentro do Termux oficial (nao via adb shell).\n' >&2
    exit 1
fi
api=$(/system/bin/getprop ro.build.version.sdk)
[[ "$api" =~ ^[0-9]+$ && "$api" -ge 26 ]] || { printf 'Android 8/API 26+ necessario.\n' >&2; exit 1; }
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ -r "$source_dir/ava-boot.sh" ]] || { printf 'Copie tambem ava-boot.sh para esta pasta.\n' >&2; exit 1; }
boot_dir="${HOME:?}/.termux/boot"
mkdir -p "$boot_dir"
target="$boot_dir/50-arandu-ava"
if [[ -f "$target" ]] && ! cmp -s "$source_dir/ava-boot.sh" "$target"; then
    mkdir -p "$HOME/.local/state/arandu-wake/backups"
    # Never put backups in boot/: Termux:Boot would execute them as extra hooks.
    cp -p "$target" "$HOME/.local/state/arandu-wake/backups/50-arandu-ava.$(date +%Y%m%d%H%M%S)"
fi
cp "$source_dir/ava-boot.sh" "$target"
chmod 700 "$target"
printf 'Hook instalado: %s\n' "$target"
printf 'Abra Termux:Boot uma vez; ative Auto start no Ava; retire restricoes de bateria para Ava/Termux/Termux:Boot. Android 10+ pode exigir permitir sobreposicao ao Termux.\n'
printf 'O hook nao concede permissoes, nao inicia captura no Termux e nao garante boot em toda ROM. Valide apos reinicio real e primeiro desbloqueio.\n'
