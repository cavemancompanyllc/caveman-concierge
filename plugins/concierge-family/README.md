# concierge-family

Tracks kids' school life: processes school email into a notes vault,
calendar events, and Google Tasks.

## Before you use this with your own kids

Everything family-specific lives in your instance, never in this plugin:

1. Copy `config/school.example.json` (in this plugin) to your instance's
   `config/school.json`. List one lowercase slug per child in `kids` — these
   are the values `school_db.py --kid` accepts. Put your school's name and
   grade labels in `title_stopwords` so event-title matching ignores them.
2. Copy `skills/school-mail/routing.example.md` to your instance's
   `config/school-mail-routing.md` and fill in your real kids, school,
   senders, and local timezone.

Keep both files out of git (`config/school.json` and
`config/school-mail-routing.md` in your instance's `.gitignore`).

Kid slugs are validated by the CLI, not by SQL `CHECK` constraints, so you
can add a child later by editing `config/school.json` — no schema migration.
