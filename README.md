# Opportunity & Network OS

A starter project for a 2027 B.Tech CSE/AI student in India with zero professional experience.

It discovers public jobs from configured Greenhouse and Lever boards, filters for early-career technical opportunities, deduplicates listings, sends email/Telegram alerts, and maintains a local application tracker. It also includes a lightweight outreach tracker so networking is based on genuine shared interests rather than mass messaging.

## What this can and cannot do

- Searches only the company boards you configure in `config/boards.json`; it does **not** crawl the whole internet.
- Public job-board APIs do not reliably expose deadlines, visa rules, degree-year criteria, or sponsorship. Treat matches as leads and verify the official posting.
- Application status and outreach notes are stored locally in SQLite. Do not put private application notes in a public GitHub repository.
- GitHub Actions runs discovery in the cloud. It stores only public job-listing state in `data/jobs.json` for deduplication. Local application/outreach tracking remains on your computer unless you later connect a private database.
- Email/Telegram alerts require credentials configured as GitHub Actions secrets (or environment variables locally).

## Project status

This is an early-stage, community-ready starter—not a production global crawler. The discovery connectors currently support configured public Greenhouse and Lever boards. CI runs syntax checks and unit tests. Contributions and issue reports are welcome.

## 1. Run in VS Code

1. Install Python 3.11+ and Git.
2. Extract this folder and open it in VS Code.
3. In the terminal:

   ```bash
   python -m venv .venv
   # Windows PowerShell:
   .\.venv\Scripts\Activate.ps1
   # macOS/Linux:
   source .venv/bin/activate
   pip install -r requirements.txt
   copy config/boards.example.json config/boards.json
   ```

4. Edit `config/boards.json`. Add Greenhouse board tokens and Lever site names for companies you want to monitor. Use official company career pages to identify the right board/site.
5. Run tests before discovery:

   ```bash
   python -m compileall -q src tests
   python -m unittest discover -s tests -v
   ```

6. Run:

   ```bash
   python -m src.radar discover
   python -m src.radar list
   python -m src.radar add-application --job-id JOB_ID --status saved
   python -m src.radar applications
   ```

## 2. Email alerts (SMTP)

Set environment variables locally or GitHub Actions secrets:

- `SMTP_HOST` (e.g. your provider's SMTP host)
- `SMTP_PORT` (usually 587)
- `SMTP_USER`
- `SMTP_PASSWORD` (use an app password where required)
- `ALERT_FROM`
- `ALERT_TO`

Do not commit passwords or `.env` files.

## 3. Telegram alerts

Create a bot using Telegram's official BotFather, then set:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Send a message to your bot and obtain your chat ID before expecting delivery. Never commit the token.

## 4. GitHub Actions setup

1. Create a GitHub repository and push this project.
2. Add the environment secrets above under **Settings → Secrets and variables → Actions**.
3. Edit `.github/workflows/job-radar.yml` and replace the placeholder branch if needed.
4. Enable Actions. The workflow runs every 6 hours and can also be run manually.
5. The workflow commits only `data/jobs.json`, which contains public job listing data for deduplication. Keep `data/tracker.db` local and untracked.

GitHub-hosted scheduled workflows can be delayed, and GitHub Actions is not a guaranteed real-time alert service.

## 5. Track applications and networking

Application statuses: `discovered`, `saved`, `to_apply`, `applied`, `assessment`, `interview`, `rejected`, `offer`, `closed`.

```bash
python -m src.radar applications
python -m src.radar add-application --job-id JOB_ID --status applied --notes "Submitted official application"
python -m src.radar add-contact --name "Public maintainer/researcher name" --organization "Org" --channel "Public GitHub profile or event" --reason "Specific technical overlap"
python -m src.radar contacts
```

Only record professional information that is public or that the person has shared with you. Do not scrape private contact details or mass-message people. Make outreach specific, short, and grounded in a real shared technical interest.

## 6. A credible way to stand out

The tracker alone will not make you stand out. Use it to build evidence:
- publish a useful technical project with a clear README and tests;
- contribute small, merged fixes to open-source projects;
- write short technical notes explaining what you built and what failed;
- contact maintainers/researchers with a concrete question or contribution, not a generic request for a job;
- record follow-ups and respect non-responses.

## Data and limitations

The local tracker database is created at `data/tracker.db`. Public listing state is stored in `data/jobs.json`. The first run may return many leads. Keyword filters are deliberately explainable and imperfect; inspect official postings before applying.


## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow and [LICENSE](LICENSE) for license terms. CI runs on pushes and pull requests.
