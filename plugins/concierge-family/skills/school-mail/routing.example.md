# School Mail Routing Table (example)

The real routing table is instance data, not part of this plugin — it
names real children, family email addresses, and school staff, and should
be gitignored in your instance repo. Copy this file to
`config/school-mail-routing.md` at your instance root and fill it in.
`SKILL.md` expects the sections below.

## Kids

| Kid | Legal name | This year | Last year |
|---|---|---|---|
| Kid1 | Full Name | Nth grade | Nth grade |

School, district, principal, dismissal/walking arrangements. Parents' email
addresses. Student IDs live in `school_db.py kv student_id_<kid>`, not here.

## Search queries

Gmail queries the daily scan runs, each ending `after:<epoch>`: one
`from:(domain OR domain ...)` over every known sender (minus exclusions),
any parent-to-teacher query, and a catch-all on the kids' names and school.

## Buckets

For each bucket (`<kid>`, `school`, `district`): a table of sender address,
who they are, which school year they apply to, plus the Gmail label.

### Kid determined from body
Senders whose mail covers either kid (attendance notices, signups, clubs,
sports) and the rule for telling which kid it's about.

### school / district
Campus vs district senders. Note any shared address that must be split by
display name or subject.

## Hard exclusions
Senders/patterns that must never be processed (a parent's own work mail at
the district, known compromised accounts, kids' personal account mail).

## Unknown senders
Logged as `unrouted`, never create events/tasks.

## Gmail label set
`School`, `School/<Kid>`, `School/<SchoolName>`, `School/District`, `School/PTA`,
`School/Signups`, `School/Activities`, `School/Attendance`.
