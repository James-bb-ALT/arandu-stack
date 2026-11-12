"""Configuration flow for the local passive add-on endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    import probatio as vol
except ImportError:  # Home Assistant versions before the schema migration.
    import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

from .client import ClientError, LocalNluClient, normalize_endpoint
from .endpoint_discovery import installed_endpoints, resolve_endpoints, from_addons
from .const import (
    CONF_ENDPOINT,
    CONF_DECISION_ENABLED,
    CONF_LLM_ENABLED,
    CONF_SHADOW_ENABLED,
    CONF_V2_ENABLED,
    CONF_CONTEXTUAL_ENABLED,
    CONF_FALLBACK_USER_ID,
    CONF_VOICE_IDENTITY_BINDINGS,
    CONF_DEVICE_IDENTITY_BINDINGS,
    DEFAULT_ENDPOINT,
    DOMAIN,
)


class LocalNluConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure exactly one local add-on endpoint."""

    VERSION = 1

    async def async_step_hassio(self, discovery_info):
        """Offer the installed companion when Supervisor announces its app."""
        if self._async_current_entries():
            return self.async_abort(reason='already_configured')
        try:
            endpoint, panel = from_addons({discovery_info.slug: {'hostname': discovery_info.config.get('host')}})
        except (ClientError, AttributeError, TypeError):
            return self.async_abort(reason='invalid_discovery')
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        self._discovered_endpoints = (endpoint, panel)
        self.context['title_placeholders'] = {'name': 'Arandu Assistant'}
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input=None):
        errors = {}
        endpoint, panel = self._discovered_endpoints
        if self._async_current_entries():
            return self.async_abort(reason='already_configured')
        if user_input is not None:
            try:
                await LocalNluClient(async_get_clientsession(self.hass), endpoint).async_health()
            except ClientError:
                errors['base'] = 'cannot_connect'
            else:
                return self.async_create_entry(title='Arandu Assistant', data={CONF_ENDPOINT: endpoint, 'stack_endpoint': panel})
        return self.async_show_form(step_id='hassio_confirm', data_schema=vol.Schema({}),
                                   description_placeholders={'endpoint': endpoint}, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> LocalNluOptionsFlow:
        return LocalNluOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                endpoint, panel, _ = await resolve_endpoints(self.hass, async_get_clientsession(self.hass), user_input.get(CONF_ENDPOINT, ''), user_input.get('stack_endpoint', ''))
                client = LocalNluClient(
                    async_get_clientsession(self.hass), endpoint
                )
                await client.async_health()
            except (KeyError, ClientError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Arandu Assistant",
                    data={CONF_ENDPOINT: endpoint, 'stack_endpoint': panel},
                )
        try:
            default_endpoint, default_panel = installed_endpoints(self.hass)
        except ClientError:
            default_endpoint, default_panel = '', ''
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ENDPOINT,
                        default=default_endpoint,
                    ): str,
                    vol.Optional('stack_endpoint', default=default_panel): str,
                }
            ),
            errors=errors,
        )


class LocalNluOptionsFlow(config_entries.OptionsFlow):
    """Contextual defaults and explicit capability/policy configuration."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return await self.async_step_settings(user_input)
        return self.async_show_menu(step_id='init', menu_options=[
            'voice_output', 'identity_binding', 'announcements', 'intelligence', 'performance', 'settings'])

    async def async_step_announcements(self, user_input=None):
        """Review and configure area and satellite announcement destinations."""
        from homeassistant.helpers import entity_registry, area_registry
        from .announcement_delivery import is_entity_exposed
        from .announcement_options import (
            CONF_ANNOUNCEMENTS_ENABLED,
            CONF_ANNOUNCEMENTS_ALLOW_ALL,
            CONF_ANNOUNCEMENTS_DESTINATIONS,
            CONF_ANNOUNCEMENTS_TTS_ENTITY,
            CONF_ANNOUNCEMENTS_CHIME,
            CONF_ANNOUNCEMENTS_COOLDOWN_S,
            CONF_ANNOUNCEMENTS_QUIET_HOURS,
            MAX_AREAS,
            MAX_ENTITIES_PER_AREA,
        )

        options = dict(getattr(self, "_announcements_options", self.config_entry.options))
        destinations = dict(options.get(CONF_ANNOUNCEMENTS_DESTINATIONS, {}))
        errors = {}

        if user_input is not None:
            enabled = user_input.get(CONF_ANNOUNCEMENTS_ENABLED, options.get(CONF_ANNOUNCEMENTS_ENABLED, False))
            allow_all = user_input.get(CONF_ANNOUNCEMENTS_ALLOW_ALL, options.get(CONF_ANNOUNCEMENTS_ALLOW_ALL, False))
            chime = user_input.get(CONF_ANNOUNCEMENTS_CHIME, options.get(CONF_ANNOUNCEMENTS_CHIME, True))
            tts_entity = user_input.get(CONF_ANNOUNCEMENTS_TTS_ENTITY, options.get(CONF_ANNOUNCEMENTS_TTS_ENTITY, ""))
            cooldown_s = user_input.get(CONF_ANNOUNCEMENTS_COOLDOWN_S, options.get(CONF_ANNOUNCEMENTS_COOLDOWN_S, 5))

            q_start = user_input.get("quiet_hours_start", "").strip()
            q_end = user_input.get("quiet_hours_end", "").strip()
            quiet_hours = None
            if q_start and q_end:
                import re
                if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", q_start) or not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", q_end):
                    errors["base"] = "invalid_quiet_hours"
                else:
                    quiet_hours = {"start": q_start, "end": q_end}

            area_id = user_input.get("area_id")
            satellite_id = user_input.get("satellite_id")
            media_player_id = user_input.get("media_player_id")
            existing_mapping = user_input.get("existing_mapping")
            save_and_finish = user_input.get("save_and_finish", False)

            if existing_mapping:
                rem_area, _, rem_ent = existing_mapping.partition(":")
                if rem_area in destinations:
                    cur_list = [d for d in destinations[rem_area] if (d.get("entity_id") if isinstance(d, dict) else getattr(d, "entity_id", None)) != rem_ent]
                    if cur_list:
                        destinations[rem_area] = cur_list
                    else:
                        destinations.pop(rem_area, None)
            elif satellite_id and media_player_id:
                errors["base"] = "choose_one_announcement_target"
            elif (satellite_id or media_player_id) and not area_id:
                errors["base"] = "announcement_area_required"
            elif satellite_id or media_player_id:
                target_id = satellite_id or media_player_id
                target_kind = "assist_satellite" if satellite_id else "media_player"
                reg = entity_registry.async_get(self.hass)
                entry = reg.entities.get(target_id)
                if entry is None or getattr(entry, "disabled_by", None) is not None:
                    errors["base"] = "invalid_announcement_origin"
                elif not is_entity_exposed(self.hass, target_id):
                    errors["base"] = "announcement_not_exposed"
                else:
                    cur_area_list = list(destinations.get(area_id, []))
                    if len(destinations) >= MAX_AREAS and area_id not in destinations:
                        errors["base"] = "announcement_limit_reached"
                    elif len(cur_area_list) >= MAX_ENTITIES_PER_AREA:
                        errors["base"] = "announcement_limit_reached"
                    else:
                        cur_area_list = [d for d in cur_area_list if (d.get("entity_id") if isinstance(d, dict) else getattr(d, "entity_id", None)) != target_id]
                        cur_area_list.append({"kind": target_kind, "entity_id": target_id})
                        destinations[area_id] = cur_area_list

            if not errors:
                options[CONF_ANNOUNCEMENTS_ENABLED] = enabled
                options[CONF_ANNOUNCEMENTS_ALLOW_ALL] = allow_all
                options[CONF_ANNOUNCEMENTS_CHIME] = chime
                if tts_entity:
                    options[CONF_ANNOUNCEMENTS_TTS_ENTITY] = tts_entity
                else:
                    options.pop(CONF_ANNOUNCEMENTS_TTS_ENTITY, None)
                options[CONF_ANNOUNCEMENTS_COOLDOWN_S] = cooldown_s
                if quiet_hours:
                    options[CONF_ANNOUNCEMENTS_QUIET_HOURS] = quiet_hours
                else:
                    options.pop(CONF_ANNOUNCEMENTS_QUIET_HOURS, None)
                options[CONF_ANNOUNCEMENTS_DESTINATIONS] = destinations
                self._announcements_options = options

                if save_and_finish or not any((existing_mapping, satellite_id, media_player_id)):
                    from .announcement_router import get_session_store
                    get_session_store(self.hass).clear()
                    return self.async_create_entry(data=options)

        reg = entity_registry.async_get(self.hass)
        areas = area_registry.async_get(self.hass)
        rows, choices = [], []
        for a_id, ent_list in sorted(destinations.items()):
            a_entry = areas.async_get_area(a_id) if hasattr(areas, "async_get_area") else None
            a_name = getattr(a_entry, "name", a_id)
            for ent_item in ent_list:
                e_id = ent_item.get("entity_id") if isinstance(ent_item, dict) else getattr(ent_item, "entity_id", None)
                k = ent_item.get("kind") if isinstance(ent_item, dict) else getattr(ent_item, "kind", None)
                ent_entry = reg.entities.get(e_id)
                e_name = getattr(ent_entry, "name", None) or getattr(ent_entry, "original_name", None) or e_id
                label = f"{a_name} ({a_id}) -> {e_name} [{k}]"
                rows.append(label)
                choices.append({"value": f"{a_id}:{e_id}", "label": label})

        fields = {
            vol.Optional(CONF_ANNOUNCEMENTS_ENABLED, default=options.get(CONF_ANNOUNCEMENTS_ENABLED, False)): bool,
            vol.Optional(CONF_ANNOUNCEMENTS_ALLOW_ALL, default=options.get(CONF_ANNOUNCEMENTS_ALLOW_ALL, False)): bool,
            vol.Optional(CONF_ANNOUNCEMENTS_CHIME, default=options.get(CONF_ANNOUNCEMENTS_CHIME, True)): bool,
            vol.Optional(CONF_ANNOUNCEMENTS_COOLDOWN_S, default=options.get(CONF_ANNOUNCEMENTS_COOLDOWN_S, 5)): vol.All(vol.Coerce(int), vol.Range(min=3, max=60)),
            vol.Optional(CONF_ANNOUNCEMENTS_TTS_ENTITY, **({"default": options[CONF_ANNOUNCEMENTS_TTS_ENTITY]} if options.get(CONF_ANNOUNCEMENTS_TTS_ENTITY) else {})): selector.EntitySelector(selector.EntitySelectorConfig(domain="tts")),
            vol.Optional("quiet_hours_start", default=options.get(CONF_ANNOUNCEMENTS_QUIET_HOURS, {}).get("start", "")): str,
            vol.Optional("quiet_hours_end", default=options.get(CONF_ANNOUNCEMENTS_QUIET_HOURS, {}).get("end", "")): str,
            vol.Optional("area_id"): selector.AreaSelector(),
            vol.Optional("satellite_id"): selector.EntitySelector(selector.EntitySelectorConfig(domain="assist_satellite")),
            vol.Optional("media_player_id"): selector.EntitySelector(selector.EntitySelectorConfig(domain="media_player")),
            vol.Optional("save_and_finish", default=False): bool,
        }
        if choices:
            fields[vol.Optional("existing_mapping")] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=choices, mode=selector.SelectSelectorMode.DROPDOWN)
            )

        return self.async_show_form(
            step_id="announcements",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"destinations": self._overview(rows)},
        )

    async def async_step_performance(self, user_input=None):
        limits = {'performance_concurrency':(4,1,8),'performance_waiting':(16,0,32),
                  'performance_model_waiting':(4,0,8),'performance_timeout':(45,5,90),
                  'performance_scheduler_concurrency':(2,1,4),'performance_scheduler_timeout':(20,5,60),
                  'performance_scheduler_jobs':(50,1,50)}
        if user_input is not None:
            if set(user_input)-set(limits) or any(type(value) is not int or not limits[key][1] <= value <= limits[key][2] for key,value in user_input.items()):
                return self.async_abort(reason='invalid_intelligence_options')
            return self.async_create_entry(data={**self.config_entry.options,**user_input})
        return self.async_show_form(step_id='performance', data_schema=vol.Schema({
            vol.Optional(key, default=self.config_entry.options.get(key,default)):vol.All(vol.Coerce(int),vol.Range(min=low,max=high))
            for key,(default,low,high) in limits.items()}))

    @staticmethod
    def _display(value):
        # Names come from local registries; escape them in markdown descriptions.
        text = ' '.join(str(value).split()).replace('`', "'")
        for char in ('\\', '|', '[', ']', '*', '_', '<', '>'):
            text = text.replace(char, '\\' + char)
        return text

    def _origin_name(self, kind, origin):
        from homeassistant.helpers import device_registry, entity_registry
        entry = entity_registry.async_get(self.hass).entities.get(origin) if kind == 'satellite' else device_registry.async_get(self.hass).async_get(origin)
        name = getattr(entry, 'name_by_user', None) or getattr(entry, 'name', None) or getattr(entry, 'original_name', None) or origin
        return f'{name} ({origin})'

    def _overview(self, rows):
        return '\n'.join('- ' + self._display(row) for row in rows) if rows else '—'

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        users = await self._active_users()
        errors: dict[str, str] = {}
        if user_input is not None:
            configure_intelligence = user_input.pop("configure_intelligence", False)
            configure_output = user_input.pop('configure_voice_output', False)
            # Preserve options not represented by this form, including existing bindings.
            options = {**self.config_entry.options, **user_input}
            try:
                endpoint, panel, _ = await resolve_endpoints(self.hass, async_get_clientsession(self.hass), options.get(CONF_ENDPOINT, self.config_entry.data.get(CONF_ENDPOINT, '')), options.get('stack_endpoint', self.config_entry.data.get('stack_endpoint', '')))
                options.update(endpoint=endpoint, stack_endpoint=panel)
            except ClientError:
                errors['base'] = 'cannot_connect'
            if options.get(CONF_FALLBACK_USER_ID) and options[CONF_FALLBACK_USER_ID] not in users:
                errors[CONF_FALLBACK_USER_ID] = "invalid_identity_user"
            elif not errors:
                if not options.get(CONF_FALLBACK_USER_ID):
                    options.pop(CONF_FALLBACK_USER_ID, None)
                configure_bindings = options.pop("configure_identity_bindings", False)
                if configure_output:
                    self._output_options = options
                    return await self.async_step_voice_output()
                if configure_intelligence:
                    self._intelligence_options = options
                    return await self.async_step_intelligence()
                if configure_bindings:
                    self._identity_options = options
                    return await self.async_step_identity_binding()
                return self.async_create_entry(data=options)
        options = self.config_entry.options if user_input is None else {**self.config_entry.options, **user_input}
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_FALLBACK_USER_ID, default=options.get(CONF_FALLBACK_USER_ID, "")): self._user_selector(users),
                    vol.Optional(CONF_ENDPOINT, default=options.get(CONF_ENDPOINT, self.config_entry.data.get(CONF_ENDPOINT, ''))): str,
                    vol.Optional('stack_endpoint', default=options.get('stack_endpoint', self.config_entry.data.get('stack_endpoint', ''))): str,
                    vol.Optional(CONF_CONTEXTUAL_ENABLED, default=bool(options.get(CONF_CONTEXTUAL_ENABLED, True))): bool,
                    vol.Optional("session_ttl", default=options.get("session_ttl", 60)): vol.All(vol.Coerce(int), vol.Range(min=1, max=600)),
                    vol.Optional("default_area", **({"default": options["default_area"]} if options.get("default_area") else {})): selector.AreaSelector(),
                    vol.Optional("wake_words", default=options.get("wake_words", [])): selector.ObjectSelector(),
                    vol.Optional("increments", default=options.get("increments", {})): selector.ObjectSelector(),
                    vol.Optional("entity_preferences", default=options.get("entity_preferences", {})): selector.ObjectSelector(),
                    vol.Optional("person_device_bindings", default=options.get("person_device_bindings", {})): selector.ObjectSelector(),
                    vol.Optional("device_sensor_bindings", default=options.get("device_sensor_bindings", {})): selector.ObjectSelector(),
                    vol.Optional("low_battery_threshold", default=options.get("low_battery_threshold", 20)): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
                    vol.Optional("excluded_from_bulk_actions", default=options.get("excluded_from_bulk_actions", [])): selector.EntitySelector(selector.EntitySelectorConfig(multiple=True)),
                    vol.Optional("sensitive_entities", default=options.get("sensitive_entities", [])): selector.EntitySelector(selector.EntitySelectorConfig(multiple=True)),
                    vol.Optional("energy_sources", default=options.get("energy_sources", [])): selector.EntitySelector(selector.EntitySelectorConfig(multiple=True)),
                    vol.Optional("device_mappings", default=options.get("device_mappings", {})): selector.ObjectSelector(),
                    vol.Optional("intent_bindings", default=options.get("intent_bindings", {})): selector.ObjectSelector(),
                    vol.Optional(
                        CONF_V2_ENABLED,
                        default=bool(options.get(CONF_V2_ENABLED, False)),
                    ): bool,
                    vol.Optional(
                        CONF_SHADOW_ENABLED,
                        default=bool(options.get(CONF_SHADOW_ENABLED, False)),
                    ): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_voice_output(self, user_input=None):
        """Review and stage several bindings before one final save."""
        from homeassistant.helpers import device_registry, entity_registry
        from .voice_controls import output_problem
        options = dict(getattr(self, '_output_options', self.config_entry.options))
        bindings = dict(options.get('voice_output_bindings', {}))
        errors = {}
        if user_input is not None:
            satellite, device = user_input.get('satellite_id'), user_input.get('device_id')
            player = user_input.get('output_player')
            remove = user_input.get('remove_binding', False)
            existing = user_input.get('existing_binding')
            if not any((satellite, device, player, existing, remove)):
                return self.async_create_entry(data=options)
            if existing:
                if existing not in bindings or satellite or device or player:
                    errors['base'] = 'invalid_voice_output'
                else:
                    bindings.pop(existing)
            elif bool(satellite) == bool(device):
                errors['base'] = 'choose_one_identity_origin'
            else:
                registry = entity_registry.async_get(self.hass)
                origin = registry.entities.get(satellite) if satellite else device_registry.async_get(self.hass).async_get(device)
                key = ('satellite:' + satellite) if satellite else ('device:' + device)
                if remove:
                    bindings.pop(key, None)
                elif origin is None or getattr(origin, 'disabled_by', None) is not None:
                    errors['base'] = 'invalid_identity_origin'
                else:
                    problem = output_problem(self.hass, player)
                    if problem:
                        errors['base'] = problem
                    elif len(bindings) >= 128 and key not in bindings:
                        errors['base'] = 'invalid_voice_output'
                    else:
                        bindings[key] = player
            if not errors:
                options['voice_output_bindings'] = bindings
                self._output_options = options
                if user_input.get('save_and_finish', False):
                    return self.async_create_entry(data=options)
        registry = entity_registry.async_get(self.hass)
        rows, choices = [], []
        labels = {'voice_output_unavailable': 'indisponível', 'voice_output_not_exposed': 'não exposta ao Assist',
                  'voice_output_no_volume': 'sem controle de volume', None: 'volume disponível; permissões verificadas na execução'}
        for key, target in sorted(bindings.items()):
            kind, _, origin = key.partition(':')
            entry = registry.entities.get(target)
            name = getattr(entry, 'name', None) or getattr(entry, 'original_name', None) or target
            label = f'{self._origin_name(kind, origin)} → {name} ({target})'
            rows.append(label + ' — ' + labels.get(output_problem(self.hass, target), 'verifique a saída'))
            choices.append({'value': key, 'label': label})
        fields = {
            vol.Optional('satellite_id'): selector.EntitySelector(selector.EntitySelectorConfig(domain='assist_satellite')),
            vol.Optional('device_id'): selector.DeviceSelector(),
            vol.Optional('output_player'): selector.EntitySelector(selector.EntitySelectorConfig(domain='media_player')),
            vol.Optional('save_and_finish', default=False): bool,
        }
        if choices:
            fields[vol.Optional('existing_binding')] = selector.SelectSelector(selector.SelectSelectorConfig(options=choices, mode=selector.SelectSelectorMode.DROPDOWN))
        return self.async_show_form(step_id='voice_output', data_schema=vol.Schema(fields), errors=errors,
            description_placeholders={'bindings': self._overview(rows)})

    async def async_step_intelligence(self, user_input=None):
        """Routing is integration-owned and independent of process availability."""
        if user_input is not None:
            if set(user_input) - {CONF_DECISION_ENABLED, CONF_LLM_ENABLED} or any(type(value) is not bool for value in user_input.values()):
                return self.async_abort(reason="invalid_intelligence_options")
            return self.async_create_entry(data={**getattr(self, "_intelligence_options", self.config_entry.options), **user_input})
        return self.async_show_form(step_id="intelligence", data_schema=vol.Schema({
            vol.Optional(CONF_DECISION_ENABLED, default=self.config_entry.options.get(CONF_DECISION_ENABLED, False)): bool,
            vol.Optional(CONF_LLM_ENABLED, default=self.config_entry.options.get(CONF_LLM_ENABLED, False)): bool,
        }))

    async def _active_users(self) -> dict[str, Any]:
        return {user.id: user for user in await self.hass.auth.async_get_users()
                if user.is_active is True and getattr(user, "system_generated", False) is not True}

    def _user_selector(self, users: dict[str, Any]) -> selector.SelectSelector:
        # Names are shown only in the authenticated local options UI, never diagnostics/logs.
        choices = [{"value": "", "label": "—"}]
        choices.extend({"value": user_id, "label": user.name or "—"} for user_id, user in
                       sorted(users.items(), key=lambda item: ((item[1].name or "").casefold(), item[0])))
        return selector.SelectSelector(selector.SelectSelectorConfig(options=choices, mode=selector.SelectSelectorMode.DROPDOWN))

    async def async_step_identity_binding(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Add/replace/remove explicit bindings through selectors, without raw JSON."""
        from homeassistant.helpers import device_registry, entity_registry
        from .identity import IdentityError, validate_identity_options

        if not hasattr(self, '_identity_options'):
            self._identity_options = dict(self.config_entry.options)
        users = await self._active_users()
        errors: dict[str, str] = {}
        if user_input is not None:
            satellite, device = user_input.get("satellite_id"), user_input.get("device_id")
            user_id = user_input.get("identity_user_id")
            remove = user_input.get("remove_binding", False)
            existing = user_input.get("existing_binding")
            if existing:
                kind, _, origin = existing.partition(":")
                key = CONF_VOICE_IDENTITY_BINDINGS if kind == "satellite" else CONF_DEVICE_IDENTITY_BINDINGS
                if kind not in ("satellite", "device") or origin not in self._identity_options.get(key, {}):
                    errors["base"] = "invalid_identity_origin"
                else:
                    satellite, device, remove = (origin, None, True) if kind == "satellite" else (None, origin, True)
            if not any((satellite, device, user_id, existing, remove)) and not errors:
                return self.async_create_entry(data=self._identity_options)
            if errors:
                pass
            elif bool(satellite) == bool(device):
                errors["base"] = "choose_one_identity_origin"
            elif not remove and user_id not in users:
                errors["identity_user_id"] = "invalid_identity_user"
            else:
                registry = entity_registry.async_get(self.hass)
                entry = registry.entities.get(satellite) if satellite else device_registry.async_get(self.hass).async_get(device)
                if not remove and (entry is None or getattr(entry, "disabled_by", None) is not None):
                    errors["base"] = "invalid_identity_origin"
                else:
                    key = CONF_VOICE_IDENTITY_BINDINGS if satellite else CONF_DEVICE_IDENTITY_BINDINGS
                    options = dict(self._identity_options)
                    bindings = dict(options.get(key, {}))
                    if remove:
                        bindings.pop(satellite or device, None)
                    else:
                        bindings[satellite or device] = user_id
                    options[key] = bindings
                    try:
                        validate_identity_options(options)
                    except IdentityError:
                        errors["base"] = "invalid_identity_configuration"
                    else:
                        self._identity_options = options
                        if user_input.get("save_and_finish"):
                            return self.async_create_entry(data=options)
        existing, rows = [], []
        registry = entity_registry.async_get(self.hass)
        devices = device_registry.async_get(self.hass)
        for kind, key in (("satellite", CONF_VOICE_IDENTITY_BINDINGS), ("device", CONF_DEVICE_IDENTITY_BINDINGS)):
            for origin, user_id in sorted(self._identity_options.get(key, {}).items()):
                entry = registry.entities.get(origin) if kind == "satellite" else devices.async_get(origin)
                name = getattr(entry, "name_by_user", None) or getattr(entry, "name", None) or origin
                user = users.get(user_id)
                label = f"{self._origin_name(kind, origin)} → {user.name if user else 'usuário indisponível'}"
                existing.append({"value": f"{kind}:{origin}", "label": label})
                rows.append(label)
        fields = {
            vol.Optional("satellite_id"): selector.EntitySelector(selector.EntitySelectorConfig(domain="assist_satellite")),
            vol.Optional("device_id"): selector.DeviceSelector(),
            vol.Optional("identity_user_id", default=""): self._user_selector(users),
            vol.Optional("save_and_finish", default=False): bool,
        }
        if existing:
            fields[vol.Optional("existing_binding")] = selector.SelectSelector(selector.SelectSelectorConfig(options=existing, mode=selector.SelectSelectorMode.DROPDOWN))
        return self.async_show_form(step_id="identity_binding", errors=errors, data_schema=vol.Schema(fields),
            description_placeholders={'bindings': self._overview(rows)})
