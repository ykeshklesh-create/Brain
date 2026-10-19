SCHOOL PORTAL (version 6)

START
1. Keep index.html and server.py in the same folder.
2. Open Terminal / Command Prompt in that folder and run:  python server.py
   (on some Macs/Linux: python3 server.py)
3. Open the address it prints. On a phone, use the "Phone:" address
   (computer and phone on the same Wi-Fi).

DATABASE - TWO CHOICES
A) Default: a file called school.db next to server.py (SQLite). Nothing to set up.
   Back this file up.
B) Neon (cloud PostgreSQL):
   1. Create a free project at neon.com. Copy the connection string
      (starts with postgresql://, ends with ?sslmode=require).
   2. On the computer that runs server.py, run once:
          pip install "psycopg[binary]"
   3. Create a file named  database.url  next to server.py. Paste the connection
      string in it, alone on one line, and save.
      (On Windows you may name the file database.txt instead, because Windows hides the .url ending.
      Or set it as the environment variable DATABASE_URL instead.)
   4. Stop and start server.py. It prints "Database: Neon / PostgreSQL".
      Records already in school.db are copied across the first time.
   5. Developer > School database shows "Neon / PostgreSQL".
   To go back to the file database, delete database.url and restart.
   Never share the connection string: it contains your database password.
   Photos make the saved data large. Neon's free plan is limited, so keep photos small.
   The server still has to run somewhere (the school computer or a host). Neon stores the data.

CLASSES, SUBJECTS AND TEACHERS
- Classes page: add classes with their Class ID (for example NB, P8), name, level and class teacher.
- Subjects page: subjects for Nursery, P1-P3 and P4-P7.
- Teachers page: the full Add teacher form (bursar and developer add and edit; head and
  secretary can view). Pick the classes and subjects each teacher teaches.
- Nursery is now three classes: Baby Class (NB), Middle Class (NM), Top Class (NT).
  Old data that had a single Nursery class is converted automatically: its pupils move to
  Baby Class. The bursar or developer can move each pupil to Middle or Top from the Pupils page.

SUPABASE
Supabase is PostgreSQL, so it uses the same steps as Neon (B above):
   1. Create a project at supabase.com. Choose and save a database password.
   2. Click Connect at the top of the project. Choose "Session pooler" and copy the URI.
      (The "Direct connection" address only works on networks that support IPv6.
      The session pooler works on all networks. Do not use "Transaction pooler".)
   3. Replace [YOUR-PASSWORD] in the URI with your password. If the password contains
      @ # / : ? or %, write them as %40 %23 %2F %3A %3F %25. Easiest: use a password
      with only letters and numbers.
   4. pip install "psycopg[binary]", save the URI in database.url, restart server.py.
   5. In Supabase, open Table Editor: you will see the tables state, pupils, staff and history.
   The server turns on Row Level Security for these tables so Supabase's own web API
   cannot read them. Do not turn it off, and do not share the project's API keys.
   Free Supabase projects are paused after 7 days without activity (school holidays!).
   You can resume it from the dashboard, or use a paid plan to avoid pausing.

DEVELOPER ACCOUNT PROTECTION
The server refuses any change to a developer account unless the person saving
signed in as a developer. Everyone else gets "Not saved".

DEMO LOGINS
Pupil P001 / pass1 | Teacher T01 / t123 | Bursar B01 / b123
Head H01 / h123 | Secretary S01 / s123 | Dev DEV / dev123
Change these before real use.

BEFORE REAL USE
- Passwords are stored as plain text, and anyone who can reach the server can
  read the records, passwords included. Keep it on the school network only
  until password hashing and read protection are added.
- Records are saved as one unit. If two people save at the same moment, the last save wins.
