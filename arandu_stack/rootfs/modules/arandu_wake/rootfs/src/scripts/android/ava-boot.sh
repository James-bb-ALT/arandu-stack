#!/data/data/com.termux/files/usr/bin/bash
# Run by Termux:Boot, only to open Ava's UI. Audio belongs to Ava, not Termux.
set -euo pipefail
umask 077
state_dir="${HOME:?}/.local/state/arandu-wake"
mkdir -p "$state_dir"
cleanup() {
    if command -v termux-wake-unlock >/dev/null 2>&1; then termux-wake-unlock >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM
if command -v termux-wake-lock >/dev/null 2>&1; then termux-wake-lock >/dev/null 2>&1 || true; fi
log="$state_dir/boot.log"
# Rotate bounded diagnostics. No raw audio, tokens or Home Assistant responses.
[[ ! -f "$log" ]] || mv -f "$log" "$state_dir/boot.previous.log"
printf '%s Tentativa de abrir Ava apos boot/desbloqueio.\n' "$(date -Iseconds)" > "$log"
sleep 15
for attempt in 1 2 3; do
    output=$(/system/bin/am start --user current -n com.example.ava/.MainActivity 2>&1) || output="Erro: $output"
    if [[ "$output" == *"Error"* || "$output" == *"Erro"* || "$output" == *"Exception"* || "$output" == *"denied"* || "$output" == *"Background activity start"* ]]; then
        printf '%s Tentativa %s bloqueada; abrir Ava manualmente.\n' "$(date -Iseconds)" "$attempt" >> "$log"
        sleep 10
    else
        printf '%s Activity solicitada; confirmar microfone/conexao no HA.\n' "$(date -Iseconds)" >> "$log"
        exit 0
    fi
done
exit 1
