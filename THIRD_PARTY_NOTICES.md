# Third-Party Reference Notices

HunterXJob's synthesis work was informed by publicly available documentation and repository structure from the projects below. The implementation added in this repository is clean-room code written for HunterXJob. No third-party source files were copied.

| Project | Reference URL | Observed license posture | Reuse policy in HunterXJob |
|---|---|---|---|
| AI Job Agent | https://github.com/AkbarDevop/ai-job-agent | MIT | Concepts and workflow patterns only |
| Career Copilot | https://github.com/RajjjAryan/career-copilot | MIT | Concepts and evaluation patterns only |
| hh.ru-clicker | https://github.com/Vlad9572324/hh.ru-clicker | No license detected during review | Concepts only; no code reuse |
| AutoApply AI Agentic Browser Automation | https://github.com/Rayyan9477/AutoApply-AI-Agentic-Browser-Automation-for-Job-Search | No license detected during review | Concepts only; no code reuse |
| Auto-JobHunter | https://github.com/jolie-z/Auto-JobHunter | Restrictive/non-commercial terms | Concepts only; no code reuse |
| Job Search Agent | https://github.com/surapuramakhil-org/Job_search_agent | AGPL-3.0 | Concepts only; no code reuse |
| Job Apply AI Agent | https://github.com/imon333/Job-apply-AI-agent | License status treated as unclear during review | Concepts only; no code reuse |
| JobOps | https://github.com/DaKheera47/job-ops | AGPL-3.0 with Commons Clause condition | Concepts only; no code reuse |
| Jobs Applier AI Agent AIHawk | https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk | AGPL-3.0 | Concepts only; no code reuse |
| Career Ops | https://github.com/santifer/career-ops | MIT with project trademark terms | Concepts and data-contract patterns only |
| Open Grind | https://git.opengrind.org/open-grind/open-grind | MIT; unrelated product category | Generic Android release-hygiene reference only |

Repository owners retain all rights granted by their respective licenses. This notice is not legal advice and should be rechecked before any future direct dependency, vendoring, or code import.

## Runtime dependencies added for application materials (v2)

These are normal package dependencies installed from PyPI (declared in `v2/pyproject.toml`), not vendored code.

| Package | License | Use |
|---|---|---|
| ReportLab 5.x | BSD-3-Clause | PDF rendering of résumés and cover letters |
| Bitstream Vera fonts (shipped inside the ReportLab wheel) | Bitstream Vera license (permissive; fonts may be embedded, not sold alone or renamed if modified) | Embedded in generated PDFs for a Unicode text layer |
| Pillow (ReportLab dependency) | MIT-CMU (HPND) | Image support used by ReportLab |
| pypdf 6.x | BSD-3-Clause | Reading text from PDF résumés for draft import |
| PyYAML 6.x | MIT | Loading profile YAML (`yaml.safe_load` only) |
| charset-normalizer (ReportLab dependency) | MIT | Text encoding detection |

The materials renderer, truthfulness guard and résumé parser are original HunterXJob code. Ideas from the repository's own legacy `backend/` PDF code were reused; no third-party job-automation source was copied.
