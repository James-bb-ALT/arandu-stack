use std::collections::BTreeMap;

use crate::{
    Result,
    error::{invalid_dataset, invalid_projection, resource_limit},
    evaluator::EvaluationSplit,
};

pub(super) const AREAS: [&str; 20] = [
    "sala",
    "cozinha",
    "quarto",
    "escritorio",
    "corredor",
    "varanda",
    "garagem",
    "lavanderia",
    "biblioteca",
    "atelie",
    "copa",
    "despensa",
    "banheiro",
    "suite",
    "jardim",
    "porao",
    "sotao",
    "oficina",
    "estudio",
    "academia",
];

#[derive(Clone, Copy)]
pub(super) struct GeneratorContract {
    pub(super) external_intent: &'static str,
    pub(super) slug: &'static str,
    pub(super) target_noun: &'static str,
    pub(super) train_template: &'static str,
    pub(super) development_template: &'static str,
}

#[derive(Clone, Debug)]
pub(super) struct RenderedParameter {
    pub(super) text: String,
    pub(super) begin_byte: u32,
    pub(super) end_byte: u32,
}

#[derive(Clone, Debug)]
pub(super) struct RenderedOracle {
    pub(super) utterance: String,
    pub(super) parameters: BTreeMap<String, RenderedParameter>,
    pub(super) initial_predicate: RenderedParameter,
}

pub(super) fn contract(external_intent: &str) -> Option<GeneratorContract> {
    // These bytes are copied exactly from the pinned P02 specification.
    const CONTRACTS: [GeneratorContract; 20] = [
        GeneratorContract {
            external_intent: "HassTurnOff",
            slug: "hass_turn_off",
            target_noun: "interruptor",
            train_template: "desligue %{target}",
            development_template: "por favor desligue %{target}",
        },
        GeneratorContract {
            external_intent: "HassTurnOn",
            slug: "hass_turn_on",
            target_noun: "luz",
            train_template: "ligue %{target} e %{target2}",
            development_template: "por favor ligue %{target} junto com %{target2}",
        },
        GeneratorContract {
            external_intent: "HassToggle",
            slug: "hass_toggle",
            target_noun: "ventilador",
            train_template: "altere %{target}",
            development_template: "por favor mude o estado de %{target}",
        },
        GeneratorContract {
            external_intent: "HassGetState",
            slug: "hass_get_state",
            target_noun: "sensor",
            train_template: "consulte %{target}",
            development_template: "qual é o estado de %{target}",
        },
        GeneratorContract {
            external_intent: "HassNevermind",
            slug: "hass_nevermind",
            target_noun: "pedido",
            train_template: "cancele %{target}",
            development_template: "deixe %{target} para lá",
        },
        GeneratorContract {
            external_intent: "HassSetPosition",
            slug: "hass_set_position",
            target_noun: "persiana",
            train_template: "ajuste %{target} para %{position} por cento",
            development_template: "coloque %{target} em %{position} por cento",
        },
        GeneratorContract {
            external_intent: "HassStopMoving",
            slug: "hass_stop_moving",
            target_noun: "cortina",
            train_template: "pare %{target}",
            development_template: "interrompa o movimento de %{target}",
        },
        GeneratorContract {
            external_intent: "HassStartTimer",
            slug: "hass_start_timer",
            target_noun: "temporizador",
            train_template: "inicie %{target} por %{minutes} minutos e consulte o estado depois",
            development_template: "comece %{target} com %{minutes} minutos e então verifique o estado",
        },
        GeneratorContract {
            external_intent: "HassCancelTimer",
            slug: "hass_cancel_timer",
            target_noun: "temporizador",
            train_template: "cancele %{target}",
            development_template: "por favor cancele %{target}",
        },
        GeneratorContract {
            external_intent: "HassCancelAllTimers",
            slug: "hass_cancel_all_timers",
            target_noun: "temporizador",
            train_template: "cancele todos os temporizadores do setor %{scope}",
            development_template: "por favor remova cada temporizador do setor %{scope}",
        },
        GeneratorContract {
            external_intent: "HassIncreaseTimer",
            slug: "hass_increase_timer",
            target_noun: "temporizador",
            train_template: "aumente %{target} em %{minutes} minutos",
            development_template: "adicione %{minutes} minutos a %{target}",
        },
        GeneratorContract {
            external_intent: "HassDecreaseTimer",
            slug: "hass_decrease_timer",
            target_noun: "temporizador",
            train_template: "reduza %{target} em %{minutes} minutos",
            development_template: "retire %{minutes} minutos de %{target}",
        },
        GeneratorContract {
            external_intent: "HassPauseTimer",
            slug: "hass_pause_timer",
            target_noun: "temporizador",
            train_template: "pause %{target}",
            development_template: "por favor pause %{target}",
        },
        GeneratorContract {
            external_intent: "HassUnpauseTimer",
            slug: "hass_unpause_timer",
            target_noun: "temporizador",
            train_template: "continue %{target}",
            development_template: "por favor retome %{target}",
        },
        GeneratorContract {
            external_intent: "HassTimerStatus",
            slug: "hass_timer_status",
            target_noun: "temporizador",
            train_template: "consulte %{target}",
            development_template: "qual é o estado de %{target}",
        },
        GeneratorContract {
            external_intent: "HassGetCurrentDate",
            slug: "hass_get_current_date",
            target_noun: "painel",
            train_template: "mostre a data em %{target}",
            development_template: "qual é a data para %{target}",
        },
        GeneratorContract {
            external_intent: "HassGetCurrentTime",
            slug: "hass_get_current_time",
            target_noun: "relógio",
            train_template: "mostre a hora em %{target}",
            development_template: "qual é o horário para %{target}",
        },
        GeneratorContract {
            external_intent: "HassRespond",
            slug: "hass_respond",
            target_noun: "resposta",
            train_template: "responda %{message}",
            development_template: "diga como resposta %{message}",
        },
        GeneratorContract {
            external_intent: "HassBroadcast",
            slug: "hass_broadcast",
            target_noun: "alto-falante",
            train_template: "transmita %{message} em %{target}",
            development_template: "envie o aviso %{message} para %{target}",
        },
        GeneratorContract {
            external_intent: "HassClimateGetTemperature",
            slug: "hass_climate_get_temperature",
            target_noun: "termômetro",
            train_template: "consulte a temperatura em %{target}",
            development_template: "qual é a temperatura de %{target}",
        },
    ];
    CONTRACTS
        .iter()
        .copied()
        .find(|candidate| candidate.external_intent == external_intent)
}

pub(super) fn render(
    split: EvaluationSplit,
    external_intent: &str,
    index: u64,
) -> Result<RenderedOracle> {
    let contract =
        contract(external_intent).ok_or_else(|| invalid_projection("generator contract"))?;
    if !(1..=48).contains(&index) {
        return Err(invalid_dataset("generator index range"));
    }
    let target = target_value(contract.target_noun, index, 0)?;
    let target2 = target_value(contract.target_noun, index, 120)?;
    let area_index =
        usize::try_from(index - 1).map_err(|_| resource_limit("generator area index"))?;
    let area = AREAS[area_index % AREAS.len()];
    let mut parameters = BTreeMap::new();
    parameters.insert("target", target);
    parameters.insert("target2", target2);
    parameters.insert("position", ((index - 1) % 101).to_string());
    parameters.insert("minutes", index.to_string());
    parameters.insert("scope", format!("{index:03}"));
    parameters.insert("message", format!("confirmacao {index} do setor {area}"));
    let template = match split {
        EvaluationSplit::Train => contract.train_template,
        EvaluationSplit::Development => contract.development_template,
    };
    render_template(template, &parameters)
}

fn target_value(noun: &str, index: u64, offset: u64) -> Result<String> {
    let adjusted = ((index - 1)
        .checked_add(offset)
        .ok_or_else(|| resource_limit("adjusted target index"))?
        % 240)
        + 1;
    let area_index = usize::try_from(adjusted - 1)
        .map_err(|_| resource_limit("target area index"))?
        % AREAS.len();
    let number = ((adjusted - 1) / AREAS.len() as u64) + 1;
    Ok(format!("{noun} {number} do setor {}", AREAS[area_index]))
}

fn render_template(template: &str, parameters: &BTreeMap<&str, String>) -> Result<RenderedOracle> {
    let first_placeholder = template
        .find("%{")
        .ok_or_else(|| invalid_projection("generator template placeholder"))?;
    let predicate_text = template[..first_placeholder].trim();
    if predicate_text.is_empty() || !template.starts_with(predicate_text) {
        return Err(invalid_projection("generator predicate literal"));
    }
    let initial_predicate = RenderedParameter {
        text: predicate_text.to_owned(),
        begin_byte: 0,
        end_byte: u32::try_from(predicate_text.len())
            .map_err(|_| resource_limit("predicate end"))?,
    };

    let mut utterance = String::new();
    let mut rendered = BTreeMap::new();
    let mut cursor = 0_usize;
    while let Some(relative) = template[cursor..].find("%{") {
        let start = cursor + relative;
        utterance.push_str(&template[cursor..start]);
        let name_start = start + 2;
        let close = template[name_start..]
            .find('}')
            .map(|relative| name_start + relative)
            .ok_or_else(|| invalid_projection("unterminated generator placeholder"))?;
        let name = &template[name_start..close];
        let text = parameters
            .get(name)
            .ok_or_else(|| invalid_projection("unknown generator parameter"))?;
        let begin_byte =
            u32::try_from(utterance.len()).map_err(|_| resource_limit("parameter begin"))?;
        utterance.push_str(text);
        let end_byte =
            u32::try_from(utterance.len()).map_err(|_| resource_limit("parameter end"))?;
        if rendered
            .insert(
                name.to_owned(),
                RenderedParameter {
                    text: text.clone(),
                    begin_byte,
                    end_byte,
                },
            )
            .is_some()
        {
            return Err(invalid_projection("duplicate generator parameter"));
        }
        cursor = close + 1;
    }
    utterance.push_str(&template[cursor..]);
    Ok(RenderedOracle {
        utterance,
        parameters: rendered,
        initial_predicate,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn multibyte_templates_keep_utf8_byte_boundaries() {
        let rendered =
            render(EvaluationSplit::Development, "HassGetCurrentTime", 1).expect("render");
        let target = rendered.parameters.get("target").expect("target");
        assert_eq!(
            &rendered.utterance[usize::try_from(target.begin_byte).expect("begin")
                ..usize::try_from(target.end_byte).expect("end")],
            target.text
        );
        assert_eq!(rendered.initial_predicate.text, "qual é o horário para");
    }
}
