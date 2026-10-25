"""Support for the Fronius Inverter."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import aiohttp
import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.sensor import (
    PLATFORM_SCHEMA,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    ATTR_ATTRIBUTION,
    CONF_MONITORED_CONDITIONS,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    SUN_EVENT_SUNRISE,
    SUN_EVENT_SUNSET,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

_INVERTERRT_URL = 'http://{}/solar_api/v1/GetInverterRealtimeData.cgi?Scope={}&DeviceId={}&DataCollection={}'
_POWERFLOW_URL = 'http://{}/solar_api/v1/GetPowerFlowRealtimeData.fcgi'
_METER_URL = 'http://{}/solar_api/v1/GetMeterRealtimeData.cgi?Scope={}&DeviceId={}'
_ARCHIVE_URL = 'http://{}/solar_api/v1/GetArchiveData.cgi?Scope=System&StartDate={}&EndDate={}{}'
_LOGGER = logging.getLogger(__name__)

ATTRIBUTION = "Fronius Inverter Data"

CONF_IP_ADDRESS = 'ip_address'
CONF_MODEL = 'model'
CONF_DEVICE_ID = 'device_id'
CONF_SCOPE = 'scope'
CONF_UNITS = 'units'
CONF_POWER_UNITS = 'power_units'
CONF_POWERFLOW = 'powerflow'
CONF_SMARTMETER = 'smartmeter'
CONF_SMARTMETER_DEVICE_ID = 'smartmeter_device_id'
CONF_ALWAYS_LOG = 'always_log'
CONF_STRINGS = 'strings'

DEFAULT_SCAN_INTERVAL = timedelta(seconds=60)
# The Datamanager archive only gets a new sample every 5 minutes
ARCHIVE_SCAN_INTERVAL = timedelta(minutes=5)
ARCHIVE_STEP_SECONDS = 300
# A sample older than this means the inverter is asleep (night)
ARCHIVE_STALE_SECONDS = 20 * 60
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)
ARCHIVE_TIMEOUT = aiohttp.ClientTimeout(total=30)

SCOPE_TYPES = ['Device', 'System']
UNIT_TYPES = ['Wh', 'kWh', 'MWh']
POWER_UNIT_TYPES = ['W', 'kW', 'MW']
MODEL_TYPES = ['symo', 'gen24']

STRING_CHANNELS = [
    'Current_DC_String_1', 'Voltage_DC_String_1',
    'Current_DC_String_2', 'Voltage_DC_String_2',
    'Temperature_Powerstage',
]

# Fronius StatusCode values (Solar API v1 spec)
STATUS_CODES = {
    0: 'Startup', 1: 'Startup', 2: 'Startup', 3: 'Startup', 4: 'Startup',
    5: 'Startup', 6: 'Startup', 7: 'Running', 8: 'Standby',
    9: 'Bootloading', 10: 'Error', 11: 'Idle', 12: 'Ready', 13: 'Sleeping',
    255: 'Unknown',
}

# Key: ['device', 'system', 'json_key', 'name', 'unit', 'convert_units', 'icon']
# convert_units: 'power' / 'energy' (user selectable unit), 'day_energy'
# (always kWh), 'voltage' / 'current' / 'frequency' / 'temperature' (device
# class only), 'status' (text) or False.
SENSOR_TYPES = {
    'year_energy': ['inverter', True, 'YEAR_ENERGY', 'Year Energy', 'MWh', 'energy', 'mdi:solar-power'],
    'total_energy': ['inverter', True, 'TOTAL_ENERGY', 'Total Energy', 'MWh', 'energy', 'mdi:solar-power'],
    'ac_power': ['inverter', True, 'PAC', 'AC Power', 'W', 'power', 'mdi:solar-power'],
    'day_energy': ['inverter', True, 'DAY_ENERGY', 'Day Energy', 'kWh', 'day_energy', 'mdi:solar-power'],
    'ac_current': ['inverter', False, 'IAC', 'AC Current', 'A', 'current', 'mdi:solar-power'],
    'ac_1': ['ppp_inverter', False, 'IAC_L1', 'AC_1', 'A', 'current', 'mdi:solar-power'],
    'ac_2': ['ppp_inverter', False, 'IAC_L2', 'AC_2', 'A', 'current', 'mdi:solar-power'],
    'ac_3': ['ppp_inverter', False, 'IAC_L3', 'AC_3', 'A', 'current', 'mdi:solar-power'],
    'ac_voltage': ['inverter', False, 'UAC', 'AC Voltage', 'V', 'voltage', 'mdi:solar-power'],
    'ac_1_voltage': ['ppp_inverter', False, 'UAC_L1', 'AC_1 Voltage', 'V', 'voltage', 'mdi:solar-power'],
    'ac_2_voltage': ['ppp_inverter', False, 'UAC_L2', 'AC_2 Voltage', 'V', 'voltage', 'mdi:solar-power'],
    'ac_3_voltage': ['ppp_inverter', False, 'UAC_L3', 'AC_3 Voltage', 'V', 'voltage', 'mdi:solar-power'],
    'ac_frequency': ['inverter', False, 'FAC', 'AC Frequency', 'Hz', 'frequency', 'mdi:solar-power'],
    'dc_current': ['inverter', False, 'IDC', 'DC Current', 'A', 'current', 'mdi:solar-power'],
    'dc_voltage': ['inverter', False, 'UDC', 'DC Voltage', 'V', 'voltage', 'mdi:solar-power'],
    'status_code': ['inverter_status', False, 'StatusCode', 'Status', None, 'status', 'mdi:information-outline'],
    'error_code': ['inverter_status', False, 'ErrorCode', 'Error Code', None, False, 'mdi:alert-circle-outline'],
    'grid_usage': ['powerflow', False, 'P_Grid', 'Grid Usage', 'W', 'power', 'mdi:solar-power'],
    'house_load': ['powerflow', False, 'P_Load', 'House Load', 'W', 'power', 'mdi:solar-power'],
    'panel_status': ['powerflow', False, 'P_PV', 'Panel Status', 'W', 'power', 'mdi:solar-panel'],
    'rel_autonomy': ['powerflow', False, 'rel_Autonomy', 'Relative Autonomy', '%', False, 'mdi:solar-panel'],
    'rel_selfconsumption': ['powerflow', False, 'rel_SelfConsumption', ' Relative Self Consumption', '%', False, 'mdi:solar-panel'],
    'smartmeter_current_ac_phase_one': ['smartmeter', False, 'Current_AC_Phase_1', 'SmartMeter Current AC Phase 1', 'A', 'current', 'mdi:solar-power'],
    'smartmeter_current_ac_phase_two': ['smartmeter', False, 'Current_AC_Phase_2', 'SmartMeter Current AC Phase 2', 'A', 'current', 'mdi:solar-power'],
    'smartmeter_current_ac_phase_three': ['smartmeter', False, 'Current_AC_Phase_3', 'SmartMeter Current AC Phase 3', 'A', 'current', 'mdi:solar-power'],
    'smartmeter_voltage_ac_phase_one': ['smartmeter', False, 'Voltage_AC_Phase_1', 'SmartMeter Voltage AC Phase 1', 'V', 'voltage', 'mdi:solar-power'],
    'smartmeter_voltage_ac_phase_two': ['smartmeter', False, 'Voltage_AC_Phase_2', 'SmartMeter Voltage AC Phase 2', 'V', 'voltage', 'mdi:solar-power'],
    'smartmeter_voltage_ac_phase_three': ['smartmeter', False, 'Voltage_AC_Phase_3', 'SmartMeter Voltage AC Phase 3', 'V', 'voltage', 'mdi:solar-power'],
    'smartmeter_energy_ac_consumed': ['smartmeter', False, 'EnergyReal_WAC_Sum_Consumed', 'SmartMeter Energy AC Consumed', 'Wh', 'energy', 'mdi:solar-power'],
    'smartmeter_energy_ac_sold': ['smartmeter', False, 'EnergyReal_WAC_Sum_Produced', 'SmartMeter Energy AC Sold', 'Wh', 'energy', 'mdi:solar-power'],
    # Per-MPPT string data from the Datamanager archive (5 minute samples)
    'string_1_current': ['strings', False, 'Current_DC_String_1', 'String 1 Current', 'A', 'current', 'mdi:solar-panel'],
    'string_1_voltage': ['strings', False, 'Voltage_DC_String_1', 'String 1 Voltage', 'V', 'voltage', 'mdi:solar-panel'],
    'string_1_power': ['strings', False, 'Power_DC_String_1', 'String 1 Power', 'W', 'dc_power', 'mdi:solar-panel'],
    'string_1_day_energy': ['strings', False, 'Energy_DC_String_1', 'String 1 Day Energy', 'kWh', 'dc_energy', 'mdi:solar-panel'],
    'string_2_current': ['strings', False, 'Current_DC_String_2', 'String 2 Current', 'A', 'current', 'mdi:solar-panel'],
    'string_2_voltage': ['strings', False, 'Voltage_DC_String_2', 'String 2 Voltage', 'V', 'voltage', 'mdi:solar-panel'],
    'string_2_power': ['strings', False, 'Power_DC_String_2', 'String 2 Power', 'W', 'dc_power', 'mdi:solar-panel'],
    'string_2_day_energy': ['strings', False, 'Energy_DC_String_2', 'String 2 Day Energy', 'kWh', 'dc_energy', 'mdi:solar-panel'],
    'powerstage_temperature': ['strings', False, 'Temperature_Powerstage', 'Powerstage Temperature', '°C', 'temperature', 'mdi:thermometer'],
}
# the gen24 inverter has different names for some sensors
SENSOR_TYPES_GEN24 = {
    'smartmeter_current_ac_phase_one': ['smartmeter', False, 'ACBRIDGE_CURRENT_ACTIVE_MEAN_01_F32', 'SmartMeter Current AC Phase 1', 'A', 'current', 'mdi:solar-power'],
    'smartmeter_current_ac_phase_two': ['smartmeter', False, 'ACBRIDGE_CURRENT_ACTIVE_MEAN_02_F32', 'SmartMeter Current AC Phase 2', 'A', 'current', 'mdi:solar-power'],
    'smartmeter_current_ac_phase_three': ['smartmeter', False, 'ACBRIDGE_CURRENT_ACTIVE_MEAN_03_F32', 'SmartMeter Current AC Phase 3', 'A', 'current', 'mdi:solar-power'],
    'smartmeter_voltage_ac_phase_one': ['smartmeter', False, 'SMARTMETER_VOLTAGE_01_F64', 'SmartMeter Voltage AC Phase 1', 'V', 'voltage', 'mdi:solar-power'],
    'smartmeter_voltage_ac_phase_two': ['smartmeter', False, 'SMARTMETER_VOLTAGE_02_F64', 'SmartMeter Voltage AC Phase 2', 'V', 'voltage', 'mdi:solar-power'],
    'smartmeter_voltage_ac_phase_three': ['smartmeter', False, 'SMARTMETER_VOLTAGE_03_F64', 'SmartMeter Voltage AC Phase 3', 'V', 'voltage', 'mdi:solar-power'],
    'smartmeter_energy_ac_consumed': ['smartmeter', False, 'SMARTMETER_ENERGYACTIVE_CONSUMED_SUM_F64', 'SmartMeter Energy AC Consumed', 'Wh', 'energy', 'mdi:solar-power'],
    'smartmeter_energy_ac_sold': ['smartmeter', False, 'SMARTMETER_ENERGYACTIVE_PRODUCED_SUM_F64', 'SmartMeter Energy AC Sold', 'Wh', 'energy', 'mdi:solar-power'],
}

# convert_units -> (device_class, state_class)
_CLASSES = {
    'power': (SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
    'dc_power': (SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
    'energy': (SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
    'day_energy': (SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
    'dc_energy': (SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
    'voltage': (SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
    'current': (SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
    'frequency': (SensorDeviceClass.FREQUENCY, SensorStateClass.MEASUREMENT),
    'temperature': (SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
}

_SCALE = {'W': 1, 'kW': 1e3, 'MW': 1e6, 'Wh': 1, 'kWh': 1e3, 'MWh': 1e6}

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_IP_ADDRESS): cv.string,
    vol.Optional(CONF_MODEL, default="symo"): vol.In(MODEL_TYPES),
    vol.Optional(CONF_DEVICE_ID, default='1'): cv.string,
    vol.Optional(CONF_NAME, default='Fronius'): cv.string,
    vol.Optional(CONF_SCOPE, default='Device'): vol.In(SCOPE_TYPES),
    vol.Optional(CONF_UNITS, default='MWh'): vol.In(UNIT_TYPES),
    vol.Optional(CONF_POWER_UNITS, default='W'): vol.In(POWER_UNIT_TYPES),
    vol.Optional(CONF_POWERFLOW, default=False): cv.boolean,
    vol.Optional(CONF_MONITORED_CONDITIONS, default=list(SENSOR_TYPES)):
        vol.All(cv.ensure_list, [vol.In(SENSOR_TYPES)]),
    vol.Optional(CONF_SMARTMETER, default=False): cv.boolean,
    vol.Optional(CONF_SMARTMETER_DEVICE_ID, default='0'): cv.string,
    vol.Optional(CONF_ALWAYS_LOG, default=True): cv.boolean,
    vol.Optional(CONF_STRINGS, default=False): cv.boolean,
})


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the Fronius inverter sensor."""
    session = async_get_clientsession(hass)
    ip_address = config[CONF_IP_ADDRESS]
    model = config[CONF_MODEL]
    device_id = config[CONF_DEVICE_ID]
    scope = config[CONF_SCOPE]
    units = config[CONF_UNITS]
    power_units = config[CONF_POWER_UNITS]
    name = config[CONF_NAME]
    powerflow = config[CONF_POWERFLOW]
    smartmeter = config[CONF_SMARTMETER]
    smartmeter_device_id = config[CONF_SMARTMETER_DEVICE_ID]
    scan_interval = config.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    always_log = config[CONF_ALWAYS_LOG]
    strings = config[CONF_STRINGS]

    # Per-platform copy: gen24 overrides must not leak into other instances
    sensor_types = dict(SENSOR_TYPES)
    if model == 'gen24':
        _LOGGER.debug("GEN24 configured, updating sensor list")
        sensor_types.update(SENSOR_TYPES_GEN24)

    inverter_data = InverterData(session, ip_address, device_id, 'CommonInverterData', scope)
    fetchers = {
        'inverter': (inverter_data, scan_interval),
        'inverter_status': (inverter_data, None),
        'ppp_inverter': (InverterData(session, ip_address, device_id, '3PInverterData', scope), scan_interval),
    }
    if powerflow:
        fetchers['powerflow'] = (PowerflowData(session, ip_address, None, None, None), scan_interval)
    if smartmeter:
        fetchers['smartmeter'] = (SmartMeterData(session, ip_address, smartmeter_device_id, None, 'Device'), scan_interval)
    if strings:
        fetchers['strings'] = (ArchiveData(session, ip_address, device_id, None, None), ARCHIVE_SCAN_INTERVAL)

    dev = []
    used = set()
    for variable in config[CONF_MONITORED_CONDITIONS]:
        device = sensor_types[variable][0]
        if device not in fetchers:
            _LOGGER.warning("Sensor %s needs '%s: true' in the configuration, skipped", variable, device)
            continue
        fetcher = fetchers[device][0]
        convert_units = sensor_types[variable][5]
        sensor_units = sensor_types[variable][4]
        if convert_units == 'power':
            sensor_units = power_units
        elif convert_units == 'energy':
            sensor_units = units
        used.add(device)
        dev.append(FroniusSensor(fetcher, name, sensor_types[variable], variable, scope, sensor_units, always_log))

    # Only poll the endpoints that feed at least one sensor
    polled = set()
    for device in used:
        fetcher, interval = fetchers[device]
        if interval is None:
            interval = fetchers['inverter'][1]
        if id(fetcher) in polled:
            continue
        polled.add(id(fetcher))
        await fetcher.async_update()

        async def _poll(_now, fetcher=fetcher):
            await fetcher.async_update()

        async_track_time_interval(hass, _poll, interval)

    async_add_entities(dev, True)


class FroniusSensor(SensorEntity):
    """Implementation of the Fronius inverter sensor."""

    _attr_should_poll = False

    def __init__(self, device_data, name, sensor_def, sensor_type, scope, units, always_log):
        """Initialize the sensor."""
        self._client = name
        self._device = sensor_def[0]
        self._json_key = sensor_def[2]
        self._name = sensor_def[3]
        self._type = sensor_type
        self._scope = scope
        self._units = units
        self._convert_units = sensor_def[5]
        self._data = device_data
        self._always_log = always_log
        self._value = None

        self._attr_name = f'{name} {self._name}'
        # Keep the historic unique_id format so existing entities and their
        # statistics survive the upgrade
        self._attr_unique_id = f'{name} {self._name}'
        self._attr_icon = sensor_def[6]
        self._attr_native_unit_of_measurement = units
        if self._convert_units in _CLASSES:
            self._attr_device_class, self._attr_state_class = _CLASSES[self._convert_units]
        elif self._convert_units == 'status':
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = sorted(set(STATUS_CODES.values()))

    @property
    def native_value(self):
        """Return the state of the device."""
        return self._value

    @property
    def available(self):
        """Return True when the last fetch succeeded (and the sun is up, if configured)."""
        return self._data.available and self.wants_data

    @property
    def wants_data(self):
        """Return False at night when always_log is off (no need to poll then)."""
        if self._always_log or self.hass is None:
            return True
        now = dt_util.now()
        sunrise = get_astral_event_date(self.hass, SUN_EVENT_SUNRISE, now.date())
        sunset = get_astral_event_date(self.hass, SUN_EVENT_SUNSET, now.date())
        if sunrise is None or sunset is None:
            return True
        return dt_util.as_local(sunrise) <= now <= dt_util.as_local(sunset)

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        attrs = {ATTR_ATTRIBUTION: ATTRIBUTION}
        if self._device == 'strings' and self._data.sample_time is not None:
            attrs['sample_time'] = self._data.sample_time.isoformat()
        if self._convert_units == 'status' and self._data.latest_data:
            status = self._data.latest_data.get('DeviceStatus') or {}
            attrs['status_code'] = status.get('StatusCode')
        return attrs

    def _raw_value(self):
        """Extract the raw value for this sensor from the fetcher data."""
        data = self._data.latest_data
        if not data:
            return None
        if self._device == 'inverter_status':
            status = data.get('DeviceStatus')
            return None if status is None else status.get(self._json_key)
        if self._json_key not in data:
            return None
        item = data[self._json_key]
        if self._device in ('inverter', 'ppp_inverter'):
            if self._scope == 'System':
                # Sum the values of all inverters; null counts as 0
                return sum(v or 0 for v in (item.get('Values') or {}).values())
            return item.get('Value') or 0
        # powerflow, smartmeter: plain values; null counts as 0
        return item or 0

    def _update_value(self):
        if self._device == 'strings':
            # None here means "no sample" (night), not a fetch failure
            state = (self._data.latest_data or {}).get(self._json_key)
            self._value = None if state is None else round(state, 3)
            return
        state = self._raw_value()
        if state is None:
            _LOGGER.debug("State is None for %s", self._json_key)
            return
        if self._convert_units == 'status':
            self._value = STATUS_CODES.get(int(state), 'Unknown')
        elif self._convert_units in ('energy', 'power'):
            self._value = round(state / _SCALE[self._units], 3 if self._units != 'W' else 1)
        elif self._convert_units == 'day_energy':
            # day energy always gets converted to kWh
            self._value = round(state / 1000, 3)
        elif isinstance(state, (int, float)):
            self._value = round(state, 2)
        else:
            self._value = state

    async def async_added_to_hass(self):
        """Register at data provider for updates."""
        self._data.register(self)
        self._update_value()

    async def async_will_remove_from_hass(self):
        """Unregister from the data provider."""
        self._data.unregister(self)

    async def async_update(self):
        """Refresh the value from the shared fetcher data."""
        self._update_value()


class FroniusFetcher:
    """Handle Fronius API requests."""

    timeout = REQUEST_TIMEOUT

    def __init__(self, session, ip_address, device_id, data_collection, scope):
        """Initialize the data object."""
        self._session = session
        self._ip_address = ip_address
        self._device_id = device_id
        self._data_collection = data_collection
        self._scope = scope
        self._data = None
        self._sensors = set()
        self._available = False
        self._failures = 0

    @property
    def available(self):
        """Return True if the last update succeeded."""
        return self._available

    async def async_update(self):
        """Retrieve and update latest state."""
        if self._sensors and not any(sensor.wants_data for sensor in self._sensors):
            # Night with always_log off: the sensors are unavailable anyway and the
            # sleeping inverter only answers with errors (status 7/8)
            return
        error = None
        try:
            await self._update()
        except aiohttp.ClientError as err:
            error = f"connection error ({type(err).__name__})"
        except asyncio.TimeoutError:
            error = "request timeout"
        except ValueError as err:
            error = f"invalid response ({err})"
        except (KeyError, TypeError) as err:
            error = f"invalid response ({type(err).__name__})"

        if error is None:
            if self._failures > 1:
                _LOGGER.info("%s: connection restored after %d failures", type(self).__name__, self._failures)
            self._failures = 0
            self._available = True
        else:
            self._failures += 1
            # Single misses are common (Datamanager "Transfer timeout"); warn only
            # once the sensors go unavailable, then keep quiet (night, updates)
            log = _LOGGER.warning if self._failures == 2 else _LOGGER.debug
            log("%s: failed to update: %s", type(self).__name__, error)
            # Allow one missed poll before marking the sensors unavailable
            if self._failures > 1:
                self._available = False

        for sensor in list(self._sensors):
            if sensor.hass is not None:
                sensor.async_schedule_update_ha_state(True)

    async def fetch_data(self, url):
        """Retrieve data from inverter in async manner."""
        _LOGGER.debug("Requesting data from URL: %s", url)
        async with self._session.get(url, timeout=self.timeout) as response:
            if response.status != 200:
                raise ValueError(f"HTTP {response.status}")
            json_response = await response.json(content_type=None)
        status = (json_response.get('Head') or {}).get('Status') or {}
        if status.get('Code', 0) != 0:
            raise ValueError(f"API status {status.get('Code')}: {status.get('Reason')}")
        return json_response

    @property
    def latest_data(self):
        """Return the latest data object."""
        return self._data or None

    def register(self, sensor):
        """Register child sensor for update subscriptions."""
        self._sensors.add(sensor)

    def unregister(self, sensor):
        """Remove a child sensor."""
        self._sensors.discard(sensor)


class InverterData(FroniusFetcher):
    """Inverter realtime data (CommonInverterData / 3PInverterData)."""

    async def _update(self):
        url = _INVERTERRT_URL.format(self._ip_address, self._scope, self._device_id, self._data_collection)
        self._data = (await self.fetch_data(url))['Body']['Data']


class PowerflowData(FroniusFetcher):
    """Powerflow realtime data."""

    async def _update(self):
        url = _POWERFLOW_URL.format(self._ip_address)
        self._data = (await self.fetch_data(url))['Body']['Data']['Site']


class SmartMeterData(FroniusFetcher):
    """Smart meter realtime data."""

    async def _update(self):
        url = _METER_URL.format(self._ip_address, self._scope, self._device_id)
        self._data = (await self.fetch_data(url))['Body']['Data']


class ArchiveData(FroniusFetcher):
    """Per-string DC data from the Datamanager archive (5 minute samples).

    The realtime API only exposes the summed IDC/UDC; the archive keeps
    current and voltage per MPPT, which is what is needed to spot shading,
    soiling or a failing string.
    """

    timeout = ARCHIVE_TIMEOUT

    def __init__(self, *args):
        super().__init__(*args)
        self.sample_time = None

    async def _update(self):
        today = dt_util.now().date().isoformat()
        channels = ''.join(f'&Channel={c}' for c in STRING_CHANNELS)
        url = _ARCHIVE_URL.format(self._ip_address, today, today, channels)
        body = (await self.fetch_data(url))['Body']['Data']
        node = body.get(f'inverter/{self._device_id}')
        if node is None:
            # Nothing logged yet today (before sunrise)
            self._data = self._empty()
            return
        series = {ch: {int(k): v for k, v in (node['Data'].get(ch) or {}).get('Values', {}).items()}
                  for ch in STRING_CHANNELS}
        start = dt_util.parse_datetime(node['Start'])
        data = {}
        latest = max((max(s) for s in series.values() if s), default=None)
        # Sample keys are local wall-clock seconds since midnight, not elapsed seconds since Start: on a
        # DST change day Start carries the old UTC offset, so Start + seconds would be an hour off.
        self.sample_time = None if latest is None or start is None else (
            datetime.combine(start.date(), datetime.min.time()) + timedelta(seconds=latest)
        ).replace(tzinfo=dt_util.get_default_time_zone())
        stale = self.sample_time is None or (
            (dt_util.now() - self.sample_time).total_seconds() > ARCHIVE_STALE_SECONDS
        )
        for n in (1, 2):
            cur, volt = series[f'Current_DC_String_{n}'], series[f'Voltage_DC_String_{n}']
            # Daily DC energy: each sample is the mean of a 5 minute slot
            energy = sum((cur[t] or 0) * (volt.get(t) or 0) for t in cur) * ARCHIVE_STEP_SECONDS / 3600
            data[f'Energy_DC_String_{n}'] = round(energy / 1000, 3)
            if stale:
                data[f'Current_DC_String_{n}'] = 0
                data[f'Voltage_DC_String_{n}'] = 0
                data[f'Power_DC_String_{n}'] = 0
            else:
                i, u = cur.get(latest) or 0, volt.get(latest) or 0
                data[f'Current_DC_String_{n}'] = i
                data[f'Voltage_DC_String_{n}'] = u
                data[f'Power_DC_String_{n}'] = round(i * u, 1)
        temps = series['Temperature_Powerstage']
        data['Temperature_Powerstage'] = None if stale or latest not in temps else temps[latest]
        self._data = data

    @staticmethod
    def _empty():
        data = {}
        for n in (1, 2):
            for key in ('Current', 'Voltage', 'Power', 'Energy'):
                data[f'{key}_DC_String_{n}'] = 0
        data['Temperature_Powerstage'] = None
        return data
