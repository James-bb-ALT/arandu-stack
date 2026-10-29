# Aceitação física A–J

Todos os itens abaixo estão **PENDENTES DE HARDWARE**. Execute por aparelho e registre evidências, versões, modo de detecção, pipeline, cômodo e mute. Compare central/local no ReSpeaker mantendo posição, ganho e frases constantes.

| Teste | Procedimento | Evidência mínima |
|---|---|---|
| A — ativação | Diga Alexa em 20 tentativas, distâncias/ruído registrados. | Acertos, perdas, estágio wake e horário. |
| B — STT | Diga Alexa seguida de cada frase do anexo. | Transcrição conferida no debug, sem publicar falas privadas. |
| C — NLU | Use luz de teste segura, consulte temperatura e desligue uma carga não crítica autorizada. | Intenção/entidade e efeito real corretos; música depende de capacidade existente do agente. |
| D — TTS | Teste um aparelho por vez. | Som audível somente no iniciador e cômodo preservado. |
| E — sessão | Faça 20 interações seguidas, incluindo erro e cancelamento. | Sem sessão concorrente; volta à escuta; mute permanece respeitado. |
| F — reconexão | Desconecte/reconecte Wi-Fi e reinicie HA após salvar trabalho. | Retorno automático com tempo medido, sem reabrir terminal. |
| G — duplicidade | Dois satélites próximos e depois em cômodos distintos. | Frase efetiva por cliente, vencedor, conflito no HA e ausência de execução/resposta duplicada quando suportado. |
| H — falsos positivos | TV, música e próprio TTS por período registrado; ideal 2 h por cenário. | Ativações/hora e exposição, sem afirmar taxa zero com teste curto. |
| I — desempenho | Meça ambos os modos sob a mesma carga. | Detecção, início captura, fim sonoro, CPU/RAM/bateria e estabilidade por dispositivo. |
| J — reboot | Reboot dos aparelhos; Android bloqueado/desbloqueado e tela apagada. | Serviço retoma sem Activity/terminal visível quando ROM permitir; limitações anotadas. |

Use `tests/measurements.example.csv` para medições. Não preencha desconhecidos com zero. `pipeline_ms` termina em run-end, não no fim do som. Intensidade absoluta não é comparável entre hardwares e não prova qual microfone é mais próximo.
