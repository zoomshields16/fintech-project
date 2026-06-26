# fintech-project

A web-based stock valuation tool. Users enter a ticker and input
assumptions to run a discounted cash flow (DCF) model, returning a
fair value estimate and other valuation metrics.

## Architecture

- **Frontend** — HTML/CSS/JavaScript landing page where users enter a
  ticker and view results
- **Backend** — Python (FastAPI) API that runs the valuation model and
  returns results as JSON

## Status

- [x] Project structure and private repo
- [x] Frontend landing page with ticker input
- [x] Backend API endpoint (`POST /api/run-model`)
- [x] Frontend connected to backend (end-to-end with placeholder data)
- [ ] Integrate real financial model (FMP API + DCF logic)
- [ ] User-adjustable model inputs
- [ ] Output pages (Summary, DuPont, Model, DCF, Drivers)
- [ ] Deploy to Railway
- [ ] Custom domain

## Tech Stack

- Backend: Python, FastAPI, Uvicorn
- Frontend: HTML, CSS, JavaScript
- Hosting (planned): Railway