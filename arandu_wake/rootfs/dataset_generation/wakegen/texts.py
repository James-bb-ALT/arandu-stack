import random
import re
import unicodedata

from .common import stable_seed

HARD = [
    ("Arando.", "aˈɾɐ̃du", "final vogal diferente na escrita; pode haver redução do o em /u/ no PB"),
    ("Aranda.", "aˈɾɐ̃da", "vogal final"), ("Arado.", "aˈɾadu", "nasalidade e consoante"),
    ("Aranha.", "aˈɾɐ̃ɲa", "consoante palatal"), ("Arranha.", "aˈʁɐ̃ɲa", "r forte e palatal"),
    ("Miranda.", "miˈɾɐ̃da", "início distinto e final"), ("Orlando.", "oʁˈlɐ̃du", "início e grupo consonantal"),
    ("Andando.", "ɐ̃ˈdɐ̃du", "estrutura nasal próxima"), ("Armando.", "aʁˈmɐ̃du", "grupo consonantal"),
    ("Arrumando.", "aʁuˈmɐ̃du", "sílaba extra"), ("Aran.", "aˈɾɐ̃", "truncamento léxico"),
    ("Ara.", "ˈaɾa", "duas sílabas"), ("Rando.", "ˈʁɐ̃du", "início diferente"),
    ("Estou arando a terra.", None, "contexto"), ("O Orlando chegou.", None, "contexto"),
    ("A Miranda está esperando.", None, "contexto"), ("Ele está andando pela sala.", None, "contexto"),
    ("Estou arrumando a cozinha.", None, "contexto"), ("Tem uma aranha na parede.", None, "contexto"),
    ("A varanda está aberta.", None, "contexto e sílaba adicional"),
    ("O arado ficou no campo.", None, "contexto"), ("A arara pousou no galho.", None, "contexto"),
]
GENERAL = [
    "Bom dia, como você está?", "Boa noite. Até amanhã.", "Qual é a temperatura do quarto?",
    "Acende a luz da sala.", "Desliga os ventiladores.", "Toca uma música tranquila.",
    "A previsão indica chuva à tarde.", "O café está na mesa da cozinha.",
    "Você viu a chave da porta?", "Amanhã temos uma reunião às nove horas.",
    "Abra a janela, por favor.", "A criança está brincando no jardim.",
    "A campainha tocou duas vezes.", "Está tudo bem com a televisão?",
    "O volume está muito alto.", "Quantos minutos faltam para terminar?",
    "O cachorro está dormindo perto do sofá.", "Vamos preparar o almoço juntos.",
    "Preciso conferir as mensagens mais tarde.", "Hoje o ônibus passou no horário.",
    "Eu não encontrei o controle remoto.", "Muito obrigado pela ajuda.",
    "Uma notícia: o parque receberá novas árvores nesta semana.",
    "Você quer água? Sim, um copo, por favor.", "Meu nome é Marina. Qual é o seu nome?",
    "A música terminou, podemos conversar agora.", "O trânsito está mais lento nesta manhã.",
    "Qual é a distância entre as duas cidades?", "A bicicleta ficou encostada na parede.",
    "Abra a porta do armário e pegue o prato azul.",
]
COMMANDS = ["acende a luz.", "qual é a temperatura?", "toca uma música.", "desliga tudo.", "você está aí?", "desliga o ventilador."]
SCHEDULE = ("positive", "negative_hard", "positive", "negative_general", "positive", "negative_hard", "positive", "negative_general")


def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def contains_wake(text):
    return bool(re.search(r"\barandu\b", normalized(text)))


def wake_count(text):
    return len(re.findall(r"\barandu\b", normalized(text)))


def spec(config, index):
    seed = stable_seed(config["seed"], "job", index)
    rng = random.Random(seed)
    category = SCHEDULE[index % len(SCHEDULE)]
    voice_index = (index // config["voice_bank"]["samples_per_turn"]) % config["voice_bank"]["profiles"]
    result = {"category": category, "speaker_index": voice_index, "seed": seed,
              "index": index, "sample_id": f"{config['dataset_id']}_{index:08d}",
              "construction": "single_utterance", "intonation": "natural"}
    if category == "positive":
        # Ciclo exato 70/20/10 por 10 positivos, sem usar held-out para ajustes.
        positive_index = index // 2
        mode = positive_index % 10
        if mode < 7:
            result.update(text="Arandu.", kind="isolated")
        elif mode < 9:
            command = rng.choice(COMMANDS)
            result.update(text="Arandu, " + command, kind="context", command=command,
                          construction="two_syntheses_with_known_boundary")
        else:
            punctuation = rng.choice(["!", "?"])
            result.update(text="Arandu" + punctuation, kind="special", intonation="exclamativa" if punctuation == "!" else "interrogativa")
    elif category == "negative_hard":
        text, ipa, note = rng.choice(HARD)
        result.update(text=text, kind="phonetic_negative", phonetic_hint=ipa, phonetic_note=note)
    else:
        # Composição de duas frases amplia contextos sem material externo não licenciado.
        text = rng.choice(GENERAL)
        if rng.random() < 0.3:
            text += " " + rng.choice(GENERAL)
        result.update(text=text, kind="general_negative")
    if category != "positive" and contains_wake(result["text"]):
        raise ValueError("Contaminação de classe no texto negativo")
    return result
