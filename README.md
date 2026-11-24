# Regulax AI

## Local setup

1. Create and activate a Python virtual environment, then install dependencies:

   ```sh
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env`. In Supabase, open **Connect**, select the
   **Shared Pooler** connection method, and copy its PostgreSQL URI into
   `DATABASE_URL`. Use the URI Supabase provides; URL-encode any special
   characters in the database password. Django enforces SSL for this
   connection. Set `SECRET_KEY` to a private random value. `.env` is ignored by
   Git.

   The shared pooler is suitable for Django's persistent database connections
   and management commands such as migrations. When `DATABASE_URL` is blank,
   the app uses the local `db.sqlite3` file.

3. Create the Django tables in the configured database and start the app:

   ```sh
   python manage.py migrate
   python manage.py runserver
   ```

Registration creates a Django user and its `UserProfile` record in the
configured database. It also creates an initial workspace and owner
membership in the same transaction. Login checks those same Django
credentials; Django sessions are stored there as well. The first migration
also creates Django's authentication and session tables in Supabase
PostgreSQL.

For accounts created before workspaces were added, run this idempotent command
after applying migrations:

```sh
python manage.py migrate
python manage.py backfill_workspaces
```

Each account without a membership gets its own workspace. The command does not
infer shared companies from email domains or combine existing accounts.
Workspace owners can edit company, site, product category, jurisdiction, and
manage memberships from Workspace Settings. Membership management currently
adds already registered accounts; invitation email is not enabled.

Run the local test suite with Supabase disabled when the database host is not
reachable:

```sh
DATABASE_URL= python manage.py test
```
