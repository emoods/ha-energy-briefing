# Energy Briefing for Home Assistant

> ⚠️ **Personal hobby project.** I built this for my own home and share it as-is.
> There is no support, no roadmap and no warranty – see [LICENSE](LICENSE).
> Use it at your own risk and keep an eye on what it does.

Energy Briefing sends you a short, witty briefing about your solar day – written by an
LLM from your own Home Assistant data:

> **🌧️ Today: the sun is working from home**
> Rain and about 20 kWh – just enough for the house and the battery. Monday and Tuesday
> look much better, so save the big laundry for then.
> 🧺🟡 🍽️🟢 🚗🔴

- **Morning:** what today's sun is good for, the best time window, traffic lights for
  laundry, dishwasher, car charging and air-conditioner heating
- **Midday:** how the day is going and what still makes sense this afternoon
- **Evening:** outlook for tomorrow and whether the home battery lasts the night
- **Weekly review** (optional): self-consumption, money saved, EV solar share
- **5-day outlook** – and if the whole week is grey, it simply tells you that it doesn't matter
- Leaves out everything that is irrelevant today
- Briefing language: **English** or **German**

All numbers and traffic lights are calculated by the integration itself. The LLM only
turns them into readable, funny text, so it cannot invent values.

## Requirements

- Home Assistant 2026.2 or newer
- The **Energy dashboard** set up with solar, grid and (recommended) a **solar forecast**
  (Forecast.Solar, Solcast or Open-Meteo Solar Forecast)
- Optional: home battery, car / wallbox, washer, dryer and dishwasher entities, a weather entity
- An LLM provider (or none – then you get simple template texts)

## Installation (HACS)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/emoods/ha-energy-briefing`, type **Integration**
2. Download **Energy Briefing** and restart Home Assistant
3. Settings → Devices & services → **Add integration** → *Energy Briefing*

## Setup wizard

1. **Language and AI provider**
2. **Credentials** – checked right away
3. **Model** – the available models are fetched from the provider and the newest small
   "flash-lite"/"mini"/"haiku" model is suggested
4. **Your energy system** – battery, car, appliances and weather are detected automatically
   from your entities and the Energy dashboard; just confirm or adjust
5. **Delivery** – phones, times for weekdays and weekends, weekly review, prices
   (taken from the Energy dashboard)

Everything except the provider can be changed later under *Configure*.

### Supported AI providers

| Provider | Authentication | Notes |
|---|---|---|
| Google Vertex AI | Service account JSON key | Location `global` for the newest Gemini models |
| Google Gemini API | API key (AI Studio) | |
| OpenAI | API key | |
| Anthropic Claude | API key | |
| OpenAI compatible | Base URL + optional key | OpenRouter, Mistral, Groq, Ollama, LM Studio, … |
| Home Assistant AI Task | AI Task entity | Uses any AI integration you already have |
| None | – | Template texts only |

You can also set a fallback AI Task entity that is used when the main provider fails.
If everything fails, a template briefing is sent anyway.

## Entities and services

| Entity / service | Description |
|---|---|
| `sensor.energy_briefing_briefing` | State = headline. Attributes: `summary`, `details` (markdown), `lights`, `outlook`, `best_window`, `kind`, `source`, `generated_at` |
| `button.energy_briefing_create_briefing` | Create a new briefing (no notification) |
| `button.energy_briefing_create_and_send_briefing` | Create and send to the configured phones |
| `energy_briefing.generate` | Service: `kind` (morning/midday/evening), `notify` (bool); returns the briefing |

## Dashboard card

The integration ships its own card – nothing to copy or install separately.

Dashboard → ✏️ Edit → **Add card** → search for **Energy Briefing**. The sensor is
picked automatically; you can toggle traffic lights, outlook, details, buttons and footer.

```yaml
type: custom:energy-briefing-card
entity: sensor.energy_briefing_briefing
# optional, all default to true
show_lights: true
show_outlook: true
show_details: true
show_buttons: true
show_footer: true
```

The ⚙️ icon in the card (admins only) opens the integration settings.

## Settings

Settings → Devices & services → **Energy Briefing** → **Configure**:

1. Briefing language and model
2. Your energy system (battery, car, appliances, weather, base load)
3. Delivery (phones, times, weekly review, prices, evcc URL, fallback AI)

Changes apply immediately (the integration reloads).

## Privacy

The briefing data (energy numbers, weather, appliance states – no names, locations or
personal data) is sent to the AI provider you choose. Credentials stay in your Home
Assistant config entry.

## License

[MIT](LICENSE) – provided "as is", without warranty of any kind.
