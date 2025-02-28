[![HACS Badge](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/custom-components/hacs) [![Fronius Release](https://img.shields.io/github/release/safepay/sensor.fronius.svg)](https://github.com/safepay/sensor.fronius) ![Maintenance](https://img.shields.io/maintenance/no/2021.svg)

# Fronius Sensor for Home Assistant

This component simplifies the integration of a Fronius inverter—and optionally PowerFlow/SmartMeter—into Home Assistant. It provides:

- Up to 22 individual sensors for easy display or automation use.
- Conversion from Wh to kWh with values rounded to 2 decimal places.
- Conversion of yearly and total energy data to kWh or MWh (user-configurable).
- Optional summation of values for multiple inverters.
- Support for the new Gen24 inverter (note: some sensor names differ from the Symo model).
- Support for Home Assistant 2025.2.x.

If you have a SmartMeter installed, this component can also:

- Optionally connect to the PowerFlow API for 5 additional sensors.
- Optionally connect to the SmartMeter API for 8 additional sensors.
- Convert PowerFlow units to W, kW, or MW.
- Work with the custom [Power Wheel Card](https://github.com/gurbyz/power-wheel-card/tree/master).

---

## Energy Dashboard Support – HA 2021.8+

This component supports the Energy Dashboard introduced in Home Assistant 2021.8. It provides the necessary attributes for power and energy sensors to be used in the Energy Dashboard.

## Configuration

To use this component, add the following to your `configuration.yaml` file:

```yaml
sensor:
- platform: fronius_inverter
    ip_address: LOCAL_IP_FOR_FRONIUS
    model: symo
    device_id: '1'
    name: Fronius
    scope: Device
    units: MWh
    power_units: W
    powerflow: false
    monitored_conditions:
      - year_energy
      - total_energy
      - ac_power
      - day_energy
      - ac_current
      - ac_1
      - ac_2
      - ac_3
      - ac_voltage
      - ac_1_voltage
      - ac_2_voltage
      - ac_3_voltage
      - ac_frequency
      - dc_current
      - dc_voltage
      - grid_usage
      - house_load
      - panel_status
      - rel_autonomy
      - rel_selfconsumption
      - smartmeter_current_ac_phase_one
      - smartmeter_current_ac_phase_two
      - smartmeter_current_ac_phase_three
      - smartmeter_voltage_ac_phase_one
      - smartmeter_voltage_ac_phase_two
      - smartmeter_voltage_ac_phase_three
      - smartmeter_energy_ac_consumed
      - smartmeter_energy_ac_sold
    smartmeter: false
    smartmeter_device_id: '0'
    always_log: true