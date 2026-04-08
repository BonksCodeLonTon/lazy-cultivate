# cultivation-bot — Overview

> **Navigation aid.** This article shows WHERE things live (routes, models, files). Read actual source files before implementing new features or making changes.

**cultivation-bot** is a python project built with raw-http, using sqlalchemy for data persistence.

## Scale

7 database models · 11 environment variables

**Database:** sqlalchemy, 7 models — see [database.md](./database.md)

## High-Impact Files

Changes to these files have the widest blast radius across the codebase:

- `/result.py` — imported by **2** files
- `/base.py` — imported by **1** files
- `/critical.py` — imported by **1** files
- `/elemental.py` — imported by **1** files
- `/evasion.py` — imported by **1** files
- `/final_bonus.py` — imported by **1** files

## Required Environment Variables

- `DATABASE_URL` — `src\db\migrations\env.py`
- `GUILD_ID` — `src\bot\client.py`

---
_Back to [index.md](./index.md) · Generated 2026-04-08_