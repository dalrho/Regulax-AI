# Implementation Progress

## Part 0 — Inspect and verify existing login/register

**Status:** Verified with one existing UI-test mismatch recorded below.

- **Stack:** Django authentication and sessions, SQLite local fallback, Supabase PostgreSQL when `DATABASE_URL` is configured.
- **Verified:** Registration creates a Django user with a hashed password and profile; login by email redirects to the protected dashboard; invalid credentials are rejected; logout clears the session; registration validation tests pass.
- **Existing mismatch:** The complete app suite had 20 passing tests and one failure in `apps.login.tests.LoginViewTests.test_login_page_renders_correctly`. The assertion expects outdated login-page copy (`Use your individual account...`) while the current template says `Use your individual work email account...`. No Part 1 change caused this mismatch.
- **Local commands:** `pip install -r requirements.txt`, `python manage.py migrate`, `python manage.py runserver`; run tests with `DATABASE_URL= python manage.py test` when the configured Supabase host is unavailable.

## Part 1 — Add workspaces and access boundaries

**Status:** Complete.

- Added `Workspace` settings and `Membership` with `owner`/`reviewer` roles and a database uniqueness constraint for each user/workspace pair.
- Registration creates the user, profile, workspace, and owner membership atomically, without adding registration fields.
- Added idempotent `backfill_workspaces` command and on-demand dashboard onboarding for existing users. Each user receives a separate workspace; email domains are not used to infer shared membership.
- Added centralized active-membership resolution. Workspace settings and membership actions resolve the workspace from the signed-in user's membership; submitted membership IDs are checked against that workspace.
- Added owner-only settings and member management. Owners can add existing accounts, change roles, and remove members. The final owner cannot be removed or demoted.
- Dashboard now displays the resolved workspace and links owners to settings.
- **Checks:** `DATABASE_URL= python manage.py test apps.register apps.workspaces` — 15 passed. Full suite — 20 passed, one pre-existing login-page wording assertion failed. `DATABASE_URL= python manage.py check` — no issues. `DATABASE_URL= python manage.py makemigrations --check --dry-run` — no changes detected.
- **Remaining dependency:** Existing Supabase host could not be resolved in this environment. Migration generation and test verification used local SQLite; production database migrations were not run.
- **Next part:** Part 2 — SOP library and immutable versions.
