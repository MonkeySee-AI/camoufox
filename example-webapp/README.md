# Acme Mock Console

A tiny FastAPI + Jinja2 app with in-memory data, built as a playground for
browser automation. It exercises a broad set of UI features across several
pages so you can drive it from one or more tabs.

## Pages

- **Dashboard** (`/`) — stat cards, recent-projects feed, a modal popup.
- **Projects** (`/projects`) — data table, "new project" modal form, delete
  actions, success toasts.
- **Upload** (`/upload`) — multipart file upload form + list of uploaded files.
- **Contact** (`/contact`) — text inputs, select, textarea, checkbox; inbox list.
- **Components** (`/components`) — gallery of buttons, badges, alerts, inputs,
  modals, toasts and disclosures.

All state lives in memory and resets on restart.

## Run

```bash
cd example-webapp
python main.py
# or
uvicorn main:app --reload --port 8000
```

Then open http://127.0.0.1:8000
