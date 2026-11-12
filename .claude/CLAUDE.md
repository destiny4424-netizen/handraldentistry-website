# Working on this repository

## Pull requests
The owner has said Claude may merge its own pull requests into `main` without asking first.
For each change: make it, test it (for `photos/`, `clinic/` and `ortho/`, load the page in headless Chromium
and check it works), push the branch, open the pull request, merge it, then tell the owner
in plain words what changed. Don't merge a change that failed its tests or that you couldn't test;
say what's wrong instead.

## Patient data
This repository is public and `main` is published on handraldentistry.com by GitHub Pages.
Never commit patient photos, records, names or anything taken from the owner's Gmail.
The Clinic Photos app (`photos/`) keeps photos only on the phone; Ortho Assess (its own repository) keeps records in the browser and, when the doctor signs in to ☁️ sync, in `ortho.db` on the droplet (per login; served by `_clinic/server.py` under `/api/ortho/`).

## Layout
- `index.html` — public patient website
- `clinic/` — staff clinic management app
- `photos/` — Clinic Photos, the iPhone photo organiser (single HTML file, no build step)
- `ortho/` — redirect to Ortho Assess (destiny4424-netizen/ortho-assess); its cases sync through `_clinic/server.py`
- `_books/` — bookkeeping app that runs on the droplet (not published; see README)
- `_scanner/` — OrderBlock Scanner, F&O scanner on Dhan data that runs on the droplet (not published; see README)
