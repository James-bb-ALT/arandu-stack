"""Small explicit domestic compatibility feature; never resolves or executes an intent."""
OPENINGS = {'porta', 'portas', 'portao', 'portoes', 'janela', 'janelas', 'fechadura',
            'fechaduras', 'persiana', 'persianas', 'cortina', 'cortinas'}
LOCK_ACTIONS = {'tranca', 'tranque', 'trancar', 'destranca', 'destranque', 'destrancar',
                'trava', 'trave', 'destrava', 'destrave'}
OPEN_ACTIONS = {'abre', 'abra', 'abrir', 'fecha', 'feche', 'fechar'}
MEDIA = {'spotify', 'musica', 'musicas', 'radio', 'podcast', 'tv', 'televisao'}
MEDIA_ACTIONS = {'tocar', 'reproduzir', 'pausa', 'pause', 'pausar', 'retoma', 'retome'}


def compatibility(previous, candidate):
    for action in reversed(previous[-5:]):
        if action in LOCK_ACTIONS | OPEN_ACTIONS:
            return float(candidate in OPENINGS)
        if action in MEDIA_ACTIONS:
            return float(candidate in MEDIA)
    return 0.0
