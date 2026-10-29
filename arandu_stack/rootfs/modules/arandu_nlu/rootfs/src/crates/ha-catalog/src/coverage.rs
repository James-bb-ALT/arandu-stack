pub const HOME_ASSISTANT_SOURCE_ID: &str = "home-assistant-core-2026.8.3-p10-catalog";
pub const HOME_ASSISTANT_TAG: &str = "2026.8.3";
pub const HOME_ASSISTANT_COMMIT: &str = "759e4658f40b3ccb671d418b8a0ed95224bf4561";
pub const HOME_ASSISTANT_TREE: &str = "f4a72534bb33abf8b5d183910a0c134b968af2f8";
pub const HOME_ASSISTANT_LICENSE: &str = "Apache-2.0";
pub const HOME_ASSISTANT_LICENSE_SHA256: &str =
    "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4";
pub const SELECTED_PATH_BUNDLE_SHA256: &str =
    "4627587cd790e86e935c702e2f8e3c9dda48390ad639bb69cd7c7fba2fd0489e";

pub const ENTITY_PLATFORMS_PATH: &str = "homeassistant/generated/entity_platforms.py";
pub const ENTITY_PLATFORMS_BYTES: u32 = 1_329;
pub const ENTITY_PLATFORMS_SHA256: &str =
    "fc8e6bbbdf4ea942dc31759142d12d805021c19eb71a1aac02438bb3fd6ad332";
pub const INTENT_HELPER_PATH: &str = "homeassistant/helpers/intent.py";
pub const INTENT_HELPER_BYTES: u32 = 48_467;
pub const INTENT_HELPER_SHA256: &str =
    "8a62d1ab08d66a60a6c397bbb4d0b0ef12770c924ef8fd4ecffd690143703f9f";

pub const GENERATED_ENTITY_PLATFORM_DOMAINS: [&str; 45] = [
    "ai_task",
    "air_quality",
    "alarm_control_panel",
    "assist_satellite",
    "binary_sensor",
    "button",
    "calendar",
    "camera",
    "climate",
    "conversation",
    "cover",
    "date",
    "datetime",
    "device_tracker",
    "event",
    "fan",
    "geo_location",
    "humidifier",
    "image",
    "image_processing",
    "infrared",
    "lawn_mower",
    "light",
    "lock",
    "media_player",
    "notify",
    "number",
    "radio_frequency",
    "remote",
    "scene",
    "select",
    "sensor",
    "siren",
    "stt",
    "switch",
    "text",
    "time",
    "todo",
    "tts",
    "update",
    "vacuum",
    "valve",
    "wake_word",
    "water_heater",
    "weather",
];

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct IntentConstant {
    pub name: &'static str,
    pub value: &'static str,
}

pub const BUILT_IN_INTENT_CONSTANTS: [IntentConstant; 20] = [
    IntentConstant {
        name: "INTENT_TURN_OFF",
        value: "HassTurnOff",
    },
    IntentConstant {
        name: "INTENT_TURN_ON",
        value: "HassTurnOn",
    },
    IntentConstant {
        name: "INTENT_TOGGLE",
        value: "HassToggle",
    },
    IntentConstant {
        name: "INTENT_GET_STATE",
        value: "HassGetState",
    },
    IntentConstant {
        name: "INTENT_NEVERMIND",
        value: "HassNevermind",
    },
    IntentConstant {
        name: "INTENT_SET_POSITION",
        value: "HassSetPosition",
    },
    IntentConstant {
        name: "INTENT_STOP_MOVING",
        value: "HassStopMoving",
    },
    IntentConstant {
        name: "INTENT_START_TIMER",
        value: "HassStartTimer",
    },
    IntentConstant {
        name: "INTENT_CANCEL_TIMER",
        value: "HassCancelTimer",
    },
    IntentConstant {
        name: "INTENT_CANCEL_ALL_TIMERS",
        value: "HassCancelAllTimers",
    },
    IntentConstant {
        name: "INTENT_INCREASE_TIMER",
        value: "HassIncreaseTimer",
    },
    IntentConstant {
        name: "INTENT_DECREASE_TIMER",
        value: "HassDecreaseTimer",
    },
    IntentConstant {
        name: "INTENT_PAUSE_TIMER",
        value: "HassPauseTimer",
    },
    IntentConstant {
        name: "INTENT_UNPAUSE_TIMER",
        value: "HassUnpauseTimer",
    },
    IntentConstant {
        name: "INTENT_TIMER_STATUS",
        value: "HassTimerStatus",
    },
    IntentConstant {
        name: "INTENT_GET_CURRENT_DATE",
        value: "HassGetCurrentDate",
    },
    IntentConstant {
        name: "INTENT_GET_CURRENT_TIME",
        value: "HassGetCurrentTime",
    },
    IntentConstant {
        name: "INTENT_RESPOND",
        value: "HassRespond",
    },
    IntentConstant {
        name: "INTENT_BROADCAST",
        value: "HassBroadcast",
    },
    IntentConstant {
        name: "INTENT_GET_TEMPERATURE",
        value: "HassClimateGetTemperature",
    },
];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CatalogPresenceDisposition {
    Supported,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BaselineStateQueryDisposition {
    AbstainsWithoutReviewedDescriptor,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BaselineActionDisposition {
    AbstainsWithoutAllLaterContracts,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DomainCoverageDisposition {
    pub domain: &'static str,
    pub catalog_presence: CatalogPresenceDisposition,
    pub state_query: BaselineStateQueryDisposition,
    pub action: BaselineActionDisposition,
}

#[must_use]
pub fn domain_disposition(domain: &str) -> Option<DomainCoverageDisposition> {
    GENERATED_ENTITY_PLATFORM_DOMAINS
        .binary_search(&domain)
        .ok()
        .map(|index| DomainCoverageDisposition {
            domain: GENERATED_ENTITY_PLATFORM_DOMAINS[index],
            catalog_presence: CatalogPresenceDisposition::Supported,
            state_query: BaselineStateQueryDisposition::AbstainsWithoutReviewedDescriptor,
            action: BaselineActionDisposition::AbstainsWithoutAllLaterContracts,
        })
}

#[must_use]
pub fn is_pinned_domain(domain: &str) -> bool {
    domain_disposition(domain).is_some()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pinned_inventories_are_complete_unique_and_source_ordered() {
        assert_eq!(GENERATED_ENTITY_PLATFORM_DOMAINS.len(), 45);
        assert!(GENERATED_ENTITY_PLATFORM_DOMAINS.is_sorted());
        assert!(
            GENERATED_ENTITY_PLATFORM_DOMAINS
                .windows(2)
                .all(|pair| pair[0] != pair[1])
        );

        assert_eq!(BUILT_IN_INTENT_CONSTANTS.len(), 20);
        for (index, left) in BUILT_IN_INTENT_CONSTANTS.iter().enumerate() {
            assert!(
                BUILT_IN_INTENT_CONSTANTS[index + 1..]
                    .iter()
                    .all(|right| left.name != right.name && left.value != right.value)
            );
        }
    }

    #[test]
    fn every_pinned_domain_has_the_conservative_p10_disposition() {
        for domain in GENERATED_ENTITY_PLATFORM_DOMAINS {
            let disposition = domain_disposition(domain).expect("pinned domain");
            assert_eq!(
                disposition.catalog_presence,
                CatalogPresenceDisposition::Supported
            );
            assert_eq!(
                disposition.state_query,
                BaselineStateQueryDisposition::AbstainsWithoutReviewedDescriptor
            );
            assert_eq!(
                disposition.action,
                BaselineActionDisposition::AbstainsWithoutAllLaterContracts
            );
        }
        assert!(domain_disposition("fixture_tecnica_unknown").is_none());
    }
}
