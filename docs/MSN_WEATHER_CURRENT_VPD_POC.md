# MSN Weather current temperature + VPD proof of concept

## Why a bridge is required

Microsoft documents **MSN Weather** as a standard connector for Power Automate, Power Apps and Azure Logic Apps. Its `CurrentWeather` operation accepts a location (including latitude/longitude) and Metric/Imperial units, and returns current `Temperature` and `Dewpoint` among other fields.

The connector is **not documented as a standalone public REST API with a reusable API key for GitHub Actions**. A Microsoft Power Platform / Logic Apps connection resource is therefore required. Climate Pulse should not call undocumented MSN web endpoints.

For GitHub automation, use a very small HTTPS bridge on the Microsoft side:

```text
GitHub Actions
    ↓ POST { location: "lat,lon", units: "Metric" }
Power Automate / Azure Logic Apps HTTP trigger
    ↓
MSN Weather · Get current weather
    ↓
return Temperature + Dewpoint + timestamp + resolved coordinates
```

The bridge URL must be stored as a GitHub Actions secret named `MSN_WEATHER_BRIDGE_URL`. If the bridge requires a bearer token, store it as `MSN_WEATHER_BRIDGE_TOKEN`.

## Suggested bridge request

```json
{
  "location": "57.70887,11.97456",
  "units": "Metric"
}
```

The Climate Pulse script can parse either the raw connector `CurrentWeather` response or this simplified response:

```json
{
  "temperature_c": 18.2,
  "dewpoint_c": 12.4,
  "provider_created": "2026-09-05T09:00:00Z",
  "latitude": 57.71,
  "longitude": 11.97,
  "location": "Gothenburg"
}
```

## VPD calculation

Dew point represents the temperature at which the current air becomes saturated, so actual vapour pressure can be estimated as saturation vapour pressure evaluated at dew point.

```text
VPD = es(T) - es(Td)
```

where `T` is current air temperature and `Td` is current dew point. Climate Pulse uses the same saturation-vapour-pressure equation as its CRU VPD product:

```text
es = 6.1078 × exp(aT / (T + b))  hPa
```

- `a = 17.269`, `b = 237.3` for `T >= 0 °C`
- `a = 21.875`, `b = 265.5` for `T < 0 °C`

The script stores both raw VPD and a non-negative display VPD (`max(0, raw)`) to tolerate small provider rounding cases where reported dew point slightly exceeds temperature.

## Event display rule

- **Temperature:** can be shown for every event with a successful current-weather lookup.
- **VPD:** computed for every successful lookup, but shown by default only for **Drought, Wildfire and Heat**, where it is part of the existing hazard-specific climate profile.

This current-weather layer is separate from:

- CRU-TS v4.10 annual 1901–2025 long-term climate context;
- CRU 1981–2010 same-month climatology;
- any future ERA5/ERA5T event-month anomaly;
- formal event attribution.

## Connector rate limit

Microsoft documents a limit of **8 API calls per connection per 60 seconds** for the MSN Weather connector. The Climate Pulse probe therefore defaults to one request every 8 seconds. This is suitable for a limited current-event feed but is not a high-throughput global gridded weather service.

## Current status

`experiment/msn-weather-current-vpd-v1` contains the GitHub-side parser and VPD computation. Live source validation still requires a Microsoft-side Power Automate or Azure Logic Apps connection/bridge. Until `MSN_WEATHER_BRIDGE_URL` is configured, the integration remains inactive and should not be presented publicly as a live MSN data source.
