"""Base de données SQLite de l'application Portail HTIB ATLANTIS.

Uniquement la bibliothèque standard de Python (sqlite3, hashlib, secrets) :
rien à installer avec pip.
"""
import sqlite3
import hashlib
import secrets
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "htib.db")


def connexion():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def hacher_mot_de_passe(mot_de_passe: str):
    sel = secrets.token_hex(16)
    empreinte = hashlib.scrypt(mot_de_passe.encode("utf-8"), salt=bytes.fromhex(sel), n=16384, r=8, p=1).hex()
    return sel, empreinte


def verifier_mot_de_passe(mot_de_passe: str, sel: str, empreinte: str) -> bool:
    calcul = hashlib.scrypt(mot_de_passe.encode("utf-8"), salt=bytes.fromhex(sel), n=16384, r=8, p=1).hex()
    return secrets.compare_digest(calcul, empreinte)


SCHEMA = """
CREATE TABLE IF NOT EXISTS staff (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nom TEXT NOT NULL,
  prenom TEXT NOT NULL,
  identifiant TEXT UNIQUE NOT NULL,
  pass_hash TEXT NOT NULL,
  pass_salt TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'scolarite'
);

CREATE TABLE IF NOT EXISTS candidats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nom TEXT NOT NULL,
  prenom TEXT NOT NULL,
  naissance TEXT,
  sexe TEXT,
  matricule TEXT,
  profil TEXT,
  identifiant TEXT UNIQUE NOT NULL,
  pass_hash TEXT NOT NULL,
  pass_salt TEXT NOT NULL,
  carte_disponible INTEGER NOT NULL DEFAULT 0,
  ancien_parcours_id INTEGER REFERENCES programs(id),
  complement_profil TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS programs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  cycle TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS demandes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidat_id INTEGER NOT NULL REFERENCES candidats(id),
  type TEXT NOT NULL DEFAULT 'initiale',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS demandes_logement (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidat_id INTEGER NOT NULL REFERENCES candidats(id),
  type_logement TEXT NOT NULL,
  statut TEXT NOT NULL DEFAULT 'En attente',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS analyses_medicales (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidat_id INTEGER UNIQUE NOT NULL REFERENCES candidats(id),
  paye INTEGER NOT NULL DEFAULT 0,
  quittance TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rendezvous (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidat_id INTEGER NOT NULL REFERENCES candidats(id),
  motif TEXT NOT NULL,
  date_souhaitee TEXT,
  statut TEXT NOT NULL DEFAULT 'Demandé',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS voeux (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  demande_id INTEGER NOT NULL REFERENCES demandes(id),
  program_id INTEGER NOT NULL REFERENCES programs(id),
  ordre INTEGER NOT NULL,
  statut TEXT NOT NULL DEFAULT 'En attente'
);

CREATE TABLE IF NOT EXISTS ue_catalogue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL,
  libelle TEXT NOT NULL,
  semestre INTEGER NOT NULL,
  credit INTEGER NOT NULL,
  program_id INTEGER NOT NULL REFERENCES programs(id),
  obligatoire INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS inscriptions_ue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidat_id INTEGER UNIQUE NOT NULL REFERENCES candidats(id),
  confirmees INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS inscriptions_ue_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  inscription_id INTEGER NOT NULL REFERENCES inscriptions_ue(id),
  ue_id INTEGER NOT NULL REFERENCES ue_catalogue(id)
);

CREATE TABLE IF NOT EXISTS paiements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidat_id INTEGER NOT NULL REFERENCES candidats(id),
  montant INTEGER NOT NULL,
  quittance TEXT NOT NULL,
  date TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS dates_importantes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cycle TEXT NOT NULL,
  debut TEXT,
  fin TEXT,
  ordre INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS students (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nom TEXT NOT NULL,
  matricule TEXT,
  parcours TEXT,
  niveau TEXT
);
"""


def _colonnes(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def migrer(conn):
    """Ajoute les colonnes manquantes sur une base htib.db déjà existante
    (créée par une version antérieure de l'application), sans perdre les
    données déjà enregistrées."""
    ajouts = {
        "candidats": [
            ("ancien_parcours_id", "INTEGER REFERENCES programs(id)"),
            ("complement_profil", "TEXT"),
        ],
        "demandes": [
            ("type", "TEXT NOT NULL DEFAULT 'initiale'"),
        ],
    }
    for table, colonnes in ajouts.items():
        existantes = _colonnes(conn, table)
        for nom, definition in colonnes:
            if nom not in existantes:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {nom} {definition}")
    conn.commit()


def initialiser():
    premiere_fois = not os.path.exists(DB_PATH)
    conn = connexion()
    conn.executescript(SCHEMA)
    conn.commit()
    migrer(conn)

    nb_programs = conn.execute("SELECT COUNT(*) AS n FROM programs").fetchone()["n"]
    if nb_programs == 0:
        progs = [
            ("Comptabilité, Contrôle et Audit", "Licence"),
            ("Gestion Commerciale", "Licence"),
            ("Marketing Digital", "Licence"),
            ("Informatique de Gestion", "Licence"),
            ("Bureautique et Assistanat de Direction", "BTS"),
            ("Réseaux et Télécommunications", "Licence Professionnelle"),
            ("Master Professionnel en Audit et Contrôle de Gestion", "Master Professionnel"),
            ("Master Professionnel en Management de Projets", "Master Professionnel"),
        ]
        ids = []
        for nom, cycle in progs:
            cur = conn.execute("INSERT INTO programs (name, cycle) VALUES (?, ?)", (nom, cycle))
            ids.append(cur.lastrowid)

        ue = [
            ("CCA101", "Comptabilité générale", 1, 4, ids[0], 1),
            ("CCA102", "Mathématiques financières", 1, 3, ids[0], 1),
            ("CCA103", "Droit des affaires", 1, 3, ids[0], 1),
            ("CCA104", "Anglais professionnel", 1, 2, ids[0], 1),
            ("GC101", "Techniques de vente", 1, 3, ids[1], 1),
            ("GC102", "Marketing fondamental", 1, 3, ids[1], 1),
            ("INF101", "Algorithmique", 1, 3, ids[3], 1),
            ("INF102", "Bureautique avancée", 1, 2, ids[3], 0),
        ]
        conn.executemany(
            "INSERT INTO ue_catalogue (code, libelle, semestre, credit, program_id, obligatoire) VALUES (?, ?, ?, ?, ?, ?)",
            ue,
        )

        sel, empreinte = hacher_mot_de_passe("htib2026")
        conn.execute(
            "INSERT INTO staff (nom, prenom, identifiant, pass_hash, pass_salt, role) VALUES (?, ?, ?, ?, ?, ?)",
            ("Scolarité", "Service", "admin", empreinte, sel, "scolarite"),
        )
        conn.commit()
        print('Base initialisée : compte personnel par défaut -> identifiant "admin", mot de passe "htib2026" (à changer).')

    # Ajout idempotent des parcours Master Professionnel (même sur une base
    # déjà existante créée par une version antérieure sans ces parcours).
    masters = [
        ("Master Professionnel en Audit et Contrôle de Gestion", "Master Professionnel"),
        ("Master Professionnel en Management de Projets", "Master Professionnel"),
    ]
    for nom, cycle in masters:
        existe = conn.execute("SELECT 1 FROM programs WHERE name = ?", (nom,)).fetchone()
        if not existe:
            conn.execute("INSERT INTO programs (name, cycle) VALUES (?, ?)", (nom, cycle))
    conn.commit()

    # Dates importantes par défaut (modifiables ensuite par le personnel via
    # /personnel/dates) — ajoutées une seule fois, même sur une base existante.
    nb_dates = conn.execute("SELECT COUNT(*) AS n FROM dates_importantes").fetchone()["n"]
    if nb_dates == 0:
        dates_defaut = [
            ("Licence", "14/09/2026", "12/12/2026", 1),
            ("Master", "14/09/2026", "12/12/2026", 2),
            ("Doctorat", "14/09/2026", "12/12/2026", 3),
        ]
        conn.executemany(
            "INSERT INTO dates_importantes (cycle, debut, fin, ordre) VALUES (?, ?, ?, ?)",
            dates_defaut,
        )
        conn.commit()
    conn.close()
