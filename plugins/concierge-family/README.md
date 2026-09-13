# concierge-family

Tracks kids' school life: processes school email into a notes vault,
calendar events, and Google Tasks.

## Before you use this with your own kids

**Known limitation:** `scripts/school_db.py`'s SQLite schema hardcodes two
kid slugs (`rex`, `rose`) as literal `CHECK` constraint values on the
`messages`, `items`, and `roster` tables, and the same two slugs appear in
its title-similarity `STOPWORDS` set. This wasn't generalized in the first
port because doing so is a schema migration, not a text edit, and this
plugin was extracted directly from a live instance with real data already
keyed to those two slugs.

To use this with your own kids (any number, any names):

1. Edit the `CHECK (kid IN (...))` constraints in `school_db.py`'s `SCHEMA`
   string (three places: `messages`, `items`, `roster`) to your own kid
   slugs plus `'school'` and `'district'`.
2. Update the `KIDS` tuple near the top of the file to match.
3. Add your kids' first names (lowercase) to the `STOPWORDS` set if you
   want title-similarity matching to ignore them the same way.
4. Fill in `skills/school-mail/routing.example.md` → your instance's
   `config/school-mail-routing.md` with your real kids/school/senders.

A future version of this plugin may make kid slugs fully data-driven
(a `kids` table instead of a `CHECK` enum) to remove this step — tracked
as a TODO, not done yet.
