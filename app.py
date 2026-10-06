#!/usr/bin/env python3
"""Portail HTIB ATLANTIS, serveur 100% Python (bibliothèque standard uniquement).

Aucun JavaScript : chaque action est un lien ou un formulaire HTML classique,
traité par le serveur qui renvoie une page complète (comme un site web
"à l'ancienne" — PHP, CGI — mais en Python). Pas de pip install nécessaire.

Lancement : python3 app.py   (ou "py app.py" sous Windows)
"""
import html
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import urlparse, parse_qs

import db

PORT = int(os.environ.get("PORT", 3000))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# Adresse publique du site une fois le nom de domaine en place, par exemple
# "https://unigest-htib.com" (sans slash final). Tant que l'application
# tourne seulement sur localhost, cette variable reste vide et les balises
# SEO qui en dépendent (canonical, sitemap) sont simplement omises.
DOMAINE_PUBLIC = os.environ.get("DOMAINE_PUBLIC", "").rstrip("/")

# --- Sessions en mémoire : sid -> {"kind": "staff"|"candidat", "id": int} ---
SESSIONS = {}

MONTANT_SCOLARITE = 150000


def esc(x):
    return html.escape("" if x is None else str(x))


def fmt_fcfa(montant):
    return f"{montant:,}".replace(",", " ") + " FCFA"


# ----------------------------------------------------------------------------
# Mise en page commune (bandeau HTIB ATLANTIS + navigation + contenu)
# ----------------------------------------------------------------------------

DESCRIPTION_SITE = (
    "Portail HTIB ATLANTIS — inscription en ligne (Haute Technologie d'Informatique "
    "et Bureautique) : BTS, Licence Professionnelle et Master Professionnel. Inscription, "
    "réinscription, unités d'enseignement, paiement et suivi du dossier étudiant."
)


def page(titre, corps, nav_html="", top_droite="", description=None):
    canonical = f'<link rel="canonical" href="{esc(DOMAINE_PUBLIC)}/portail">' if DOMAINE_PUBLIC else ""
    desc = esc(description or DESCRIPTION_SITE)
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="{desc}">
<meta name="robots" content="index,follow">
<meta property="og:site_name" content="Portail HTIB ATLANTIS">
<meta property="og:title" content="{esc(titre)} — Portail HTIB ATLANTIS">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="website">
{canonical}
<title>{esc(titre)} — Portail HTIB ATLANTIS</title>
<link rel="icon" href="/static/logo.png">
<link rel="stylesheet" href="/static/style.css"></head>
<body>
<div class="top"><span>Haute Technologie d'Informatique et Bureautique — HTIB ATLANTIS</span><span>{top_droite}</span></div>
<header class="brand"><div class="logo"></div><div><h1>Portail HTIB ATLANTIS</h1><p>BTS · Licence Professionnelle · Master Professionnel — Gestion administrative et pédagogique</p></div></header>
<nav class="nav">{nav_html}</nav>
<main class="main">{corps}</main>
</body></html>"""


def nav_lien(href, texte, actif=False):
    cls = ' class="actif"' if actif else ""
    return f'<a href="{esc(href)}"{cls}>{esc(texte)}</a>'


def nav_visiteur(actif=""):
    return nav_lien("/portail", "Portail", actif == "portail")


def nav_candidat(actif=""):
    return (
        nav_lien("/espace", "Mon espace", actif == "espace")
        + '<form method="post" action="/deconnexion" style="display:inline"><button type="submit">Déconnexion</button></form>'
    )


def nav_staff(actif=""):
    items = [
        ("/personnel/tableau", "Tableau de bord", "tableau"),
        ("/personnel/validation", "Validation Candidatures", "validation"),
        ("/personnel/etudiants", "Étudiants", "etudiants"),
        ("/personnel/paiements", "Paiements", "paiements"),
        ("/personnel/oeuvres", "Œuvres universitaires", "oeuvres"),
    ]
    html_items = "".join(nav_lien(h, t, actif == k) for h, t, k in items)
    html_items += '<form method="post" action="/deconnexion" style="display:inline"><button type="submit">Déconnexion</button></form>'
    return html_items


def table(headers, lignes_html, vide="Aucune donnée"):
    th = "".join(f"<th>{esc(h)}</th>" for h in headers)
    corps = lignes_html if lignes_html else f'<tr><td colspan="{len(headers)}" class="muted">{esc(vide)}</td></tr>'
    return f'<table class="table"><tr>{th}</tr>{corps}</table>'


def badge(statut):
    positifs = ("Accordé", "Payé", "Confirmé", "Disponible")
    negatifs = ("Rejeté", "Annulé")
    cls = "accorde" if statut in positifs else "rejete" if statut in negatifs else "attente"
    return f'<span class="badge badge-{cls}">{esc(statut)}</span>'


def erreur_box(msg):
    return f'<div class="erreur">{esc(msg)}</div>' if msg else ""


# ----------------------------------------------------------------------------
# Accès aux données (petites fonctions au-dessus de sqlite3)
# ----------------------------------------------------------------------------

def programme(conn, pid):
    return conn.execute("SELECT * FROM programs WHERE id = ?", (pid,)).fetchone()


def demande_de_candidat(conn, candidat_id):
    return conn.execute(
        "SELECT * FROM demandes WHERE candidat_id = ? ORDER BY id DESC LIMIT 1", (candidat_id,)
    ).fetchone()


def voeux_de_demande(conn, demande_id):
    return conn.execute("SELECT * FROM voeux WHERE demande_id = ? ORDER BY ordre", (demande_id,)).fetchall()


def voeu_accorde_de_candidat(conn, candidat_id):
    return conn.execute(
        """SELECT v.* FROM voeux v JOIN demandes d ON v.demande_id = d.id
           WHERE d.candidat_id = ? AND v.statut = 'Accordé' LIMIT 1""",
        (candidat_id,),
    ).fetchone()


def paiement_de_candidat(conn, candidat_id):
    return conn.execute(
        "SELECT * FROM paiements WHERE candidat_id = ? ORDER BY id DESC LIMIT 1", (candidat_id,)
    ).fetchone()


def inscription_de_candidat(conn, candidat_id):
    return conn.execute("SELECT * FROM inscriptions_ue WHERE candidat_id = ?", (candidat_id,)).fetchone()


def logement_de_candidat(conn, candidat_id):
    return conn.execute(
        "SELECT * FROM demandes_logement WHERE candidat_id = ? ORDER BY id DESC LIMIT 1", (candidat_id,)
    ).fetchone()


def medicale_de_candidat(conn, candidat_id):
    return conn.execute("SELECT * FROM analyses_medicales WHERE candidat_id = ?", (candidat_id,)).fetchone()


def rdv_de_candidat(conn, candidat_id):
    return conn.execute(
        "SELECT * FROM rendezvous WHERE candidat_id = ? ORDER BY id DESC", (candidat_id,)
    ).fetchall()


def identifiant_depuis_nom(conn, nom):
    import unicodedata
    base = unicodedata.normalize("NFKD", nom or "cand").encode("ascii", "ignore").decode().lower()
    base = "".join(c for c in base if c.isalpha())[:10] or "cand"
    while True:
        candidat = base + str(secrets.randbelow(900) + 100)
        if not conn.execute("SELECT 1 FROM candidats WHERE identifiant = ?", (candidat,)).fetchone():
            return candidat


# ----------------------------------------------------------------------------
# Gestionnaire HTTP
# ----------------------------------------------------------------------------

class Gestionnaire(BaseHTTPRequestHandler):
    server_version = "PortailHTIB/1.0"

    # -- utilitaires requête/réponse --
    def session(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        if "sid" not in cookie:
            return None
        return SESSIONS.get(cookie["sid"].value)

    def exiger(self, kind):
        s = self.session()
        if not s or s["kind"] != kind:
            self.rediriger("/portail" if kind == "candidat" else "/personnel/connexion")
            return None
        return s

    def lire_formulaire(self):
        longueur = int(self.headers.get("Content-Length", 0) or 0)
        corps = self.rfile.read(longueur).decode("utf-8") if longueur else ""
        return parse_qs(corps, keep_blank_values=True)

    def champ(self, donnees, nom, defaut=""):
        return donnees.get(nom, [defaut])[0]

    def envoyer_html(self, corps, statut=200, set_cookie=None):
        data = corps.encode("utf-8")
        self.send_response(statut)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(data)

    def rediriger(self, vers, set_cookie=None):
        self.send_response(303)
        self.send_header("Location", vers)
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()

    def servir_statique(self, chemin):
        fichier = os.path.join(STATIC_DIR, chemin.lstrip("/"))
        if not os.path.abspath(fichier).startswith(STATIC_DIR) or not os.path.isfile(fichier):
            self.send_response(404)
            self.end_headers()
            return
        types = {".css": "text/css; charset=utf-8", ".png": "image/png", ".ico": "image/x-icon"}
        ext = os.path.splitext(fichier)[1]
        with open(fichier, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", types.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass  # silence les logs d'accès par défaut

    def servir_robots(self):
        corps = (
            "User-agent: *\n"
            "Allow: /portail\n"
            "Allow: /identification\n"
            "Disallow: /espace\n"
            "Disallow: /personnel\n"
            "Disallow: /inscription\n"
            "Disallow: /connexion-candidat\n"
        )
        if DOMAINE_PUBLIC:
            corps += f"\nSitemap: {DOMAINE_PUBLIC}/sitemap.xml\n"
        data = corps.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def servir_sitemap(self):
        base = DOMAINE_PUBLIC or f"http://{self.headers.get('Host', 'localhost')}"
        pages = ["/portail", "/portail?vue=offres", "/portail?vue=guide"]
        urls = "".join(f"<url><loc>{esc(base + p)}</loc></url>" for p in pages)
        corps = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
        data = corps.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -- routage --
    def do_GET(self):
        chemin = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)
        if chemin.startswith("/static/"):
            return self.servir_statique(chemin[len("/static/"):])
        if chemin == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if chemin == "/robots.txt":
            return self.servir_robots()
        if chemin == "/sitemap.xml":
            return self.servir_sitemap()
        routes_get = {
            "/": lambda: self.rediriger("/portail"),
            "/portail": self.vue_portail,
            "/identification": self.vue_identification,
            "/espace": self.vue_espace_tableau,
            "/espace/parcours": self.vue_espace_parcours,
            "/espace/ue": self.vue_espace_ue,
            "/espace/paiement": self.vue_espace_paiement,
            "/espace/fiche": self.vue_espace_fiche,
            "/espace/depot": self.vue_espace_depot,
            "/espace/resultats/notes": self.vue_resultats_notes,
            "/espace/resultats/cursus": self.vue_resultats_cursus,
            "/espace/resultats/releves": self.vue_resultats_releves,
            "/espace/oeuvres/logement": self.vue_oeuvres_logement,
            "/espace/oeuvres/medicale": self.vue_oeuvres_medicale,
            "/espace/oeuvres/rdv": self.vue_oeuvres_rdv,
            "/personnel/connexion": self.vue_staff_connexion,
            "/personnel/tableau": self.vue_staff_tableau,
            "/personnel/validation": self.vue_staff_validation,
            "/personnel/etudiants": self.vue_staff_etudiants,
            "/personnel/paiements": self.vue_staff_paiements,
            "/personnel/oeuvres": self.vue_staff_oeuvres,
        }
        gest = routes_get.get(chemin)
        if gest:
            try:
                return gest(qs)
            except TypeError:
                return gest()
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        chemin = urlparse(self.path).path
        routes_post = {
            "/inscription": self.post_inscription,
            "/connexion-candidat": self.post_connexion_candidat,
            "/personnel/connexion": self.post_staff_connexion,
            "/deconnexion": self.post_deconnexion,
            "/espace/parcours": self.post_espace_parcours,
            "/espace/ue": self.post_espace_ue,
            "/espace/ue/retirer": self.post_espace_ue_retirer,
            "/espace/paiement": self.post_espace_paiement,
            "/espace/oeuvres/logement": self.post_oeuvres_logement,
            "/espace/oeuvres/medicale": self.post_oeuvres_medicale,
            "/espace/oeuvres/rdv": self.post_oeuvres_rdv,
            "/personnel/voeu-statut": self.post_voeu_statut,
            "/personnel/carte": self.post_carte,
            "/personnel/etudiants": self.post_staff_etudiants,
            "/personnel/logement-statut": self.post_staff_logement_statut,
            "/personnel/rdv-statut": self.post_staff_rdv_statut,
        }
        gest = routes_post.get(chemin)
        if gest:
            return gest()
        self.send_response(404)
        self.end_headers()

    # ------------------------------------------------------------------
    # Portail public
    # ------------------------------------------------------------------
    def vue_portail(self, qs=None):
        qs = qs or {}
        vue = (qs.get("vue") or ["accueil"])[0]
        erreur = (qs.get("erreur") or [""])[0]
        conn = db.connexion()
        if vue == "offres":
            progs = conn.execute("SELECT * FROM programs ORDER BY name").fetchall()
            cartes = "".join(f'<div class="card"><h3>{esc(p["name"])}</h3><p class="muted">{esc(p["cycle"])}</p></div>' for p in progs)
            contenu_gauche = f"<h2>Offres de formation</h2><div class=\"cards\">{cartes}</div>"
        else:
            lignes = "".join(f"<tr><td>{c}</td><td>14/09/2026</td><td>12/12/2026</td></tr>" for c in ["Licence", "Master", "Doctorat"])
            contenu_gauche = f"<h2>Dates importantes</h2><div class=\"card\"><h3>Inscriptions en ligne — Année 2026-2027</h3>{table(['Cycle', 'Début', 'Fin'], lignes)}</div>"
        conn.close()

        side = f"""<aside class="side">
          <a href="/portail?vue=accueil" class="{'active' if vue == 'accueil' else ''}">Accueil</a>
          <a href="/portail?vue=offres" class="{'active' if vue == 'offres' else ''}">Offres de formation</a>
          <a href="/portail?vue=guide" class="{'active' if vue == 'guide' else ''}">Guide utilisateur</a>
          <a href="/personnel/connexion" class="deconnexion" style="color:#063b78;border-top:1px solid #e5eaf0;margin-top:8px;padding-top:12px">Espace personnel</a>
        </aside>"""
        if vue == "guide":
            contenu_gauche = "<h2>Guide utilisateur</h2><div class=\"card\"><p>Créez un compte selon votre profil, déposez vos vœux de parcours, puis suivez les étapes (unités d'enseignement, paiement, fiche d'inscription) une fois votre parcours accordé par le service de la scolarité.</p></div>"

        profils = [
            ("Nouveau bachelier", "Titulaire d'un BAC obtenu cette année, première inscription."),
            ("Ancien bachelier", "BAC obtenu les années précédentes, première inscription dans l'établissement."),
            ("Étudiant", "Déjà inscrit à HTIB ATLANTIS : réinscription ou demande de réorientation."),
            ("BAC étranger", "BAC obtenu hors du Togo, première inscription (dossier d'équivalence)."),
            ("Master Professionnel", "Titulaire d'une Licence ou d'un BTS, inscription en Master Pro."),
        ]
        profils_html = "".join(
            f'<div class="profil-item"><h4>{esc(n)} ?</h4><p>{esc(d)}</p>'
            f'<a class="btn" style="display:block;text-align:center;text-decoration:none" href="/identification?profil={esc(n)}">Créer un compte</a></div>'
            for n, d in profils
        )
        droite = f"""<div class="side" style="flex-direction:column;gap:0;padding:0">
          <div class="card"><h3>J'ai un compte</h3>
            {erreur_box(erreur)}
            <form class="form" method="post" action="/connexion-candidat">
              <input name="identifiant" placeholder="Identifiant" required>
              <input name="motDePasse" type="password" placeholder="Mot de passe" required>
              <button class="btn" type="submit">Connexion</button>
            </form>
          </div>
          <div class="card"><h3>Je n'ai pas de compte</h3>{profils_html}</div>
        </div>"""
        corps = f'<div class="portail-wrap">{side}<div>{contenu_gauche}</div>{droite}</div>'
        self.envoyer_html(page("Portail", corps, nav_visiteur("portail")))

    def vue_identification(self, qs=None):
        qs = qs or {}
        profil = (qs.get("profil") or ["Candidat"])[0]
        champs_specifiques = ""
        if profil == "Étudiant":
            conn = db.connexion()
            progs = conn.execute("SELECT * FROM programs ORDER BY name").fetchall()
            conn.close()
            options = "".join(f'<option value="{p["id"]}">{esc(p["name"])}</option>' for p in progs)
            champs_specifiques = (
                '<input name="matricule" placeholder="Numéro matricule" required>'
                f'<select name="ancienParcours" required><option value="">Parcours actuellement suivi</option>{options}</select>'
            )
        elif profil == "Master Professionnel":
            champs_specifiques = '<input name="complement" placeholder="Dernier diplôme obtenu (Licence, BTS...)" required>'
        elif profil == "BAC étranger":
            champs_specifiques = '<input name="complement" placeholder="Pays d\'obtention du BAC" required>'
        corps = f"""<div class="fiche" style="max-width:520px;margin:0 auto">
        <h2>Création de compte — {esc(profil)}</h2>
        <p class="muted">Identifiez-vous puis choisissez votre mot de passe. Un identifiant sera généré automatiquement à partir de votre nom.</p>
        <form class="form" method="post" action="/inscription">
          <input type="hidden" name="profil" value="{esc(profil)}">
          <input name="nom" placeholder="Nom" required>
          <input name="prenom" placeholder="Prénom" required>
          <input name="naissance" type="date" required>
          <select name="sexe" required><option value="">Sexe</option><option>Féminin</option><option>Masculin</option></select>
          {champs_specifiques}
          <input name="motDePasse" type="password" placeholder="Mot de passe" required>
          <input name="motDePasseConfirme" type="password" placeholder="Confirmer le mot de passe" required>
          <button class="btn" type="submit">Créer mon compte</button>
        </form>
        <p><a href="/portail">← Retour au portail</a></p>
        </div>"""
        self.envoyer_html(page("Création de compte", corps, nav_visiteur()))

    def post_inscription(self):
        donnees = self.lire_formulaire()
        nom = self.champ(donnees, "nom").strip()
        prenom = self.champ(donnees, "prenom").strip()
        mdp1 = self.champ(donnees, "motDePasse")
        mdp2 = self.champ(donnees, "motDePasseConfirme")
        profil = self.champ(donnees, "profil")
        if not nom or not prenom or not mdp1:
            return self.rediriger("/identification?profil=" + profil)
        if mdp1 != mdp2:
            return self.rediriger("/identification?profil=" + profil)
        conn = db.connexion()
        identifiant = identifiant_depuis_nom(conn, nom)
        sel, empreinte = db.hacher_mot_de_passe(mdp1)
        ancien_parcours = self.champ(donnees, "ancienParcours") or None
        complement = self.champ(donnees, "complement") or None
        cur = conn.execute(
            "INSERT INTO candidats (nom, prenom, naissance, sexe, matricule, profil, identifiant, pass_hash, pass_salt, ancien_parcours_id, complement_profil) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (nom, prenom, self.champ(donnees, "naissance"), self.champ(donnees, "sexe"), self.champ(donnees, "matricule"), profil, identifiant, empreinte, sel, ancien_parcours, complement),
        )
        conn.commit()
        candidat_id = cur.lastrowid
        conn.close()
        sid = secrets.token_hex(24)
        SESSIONS[sid] = {"kind": "candidat", "id": candidat_id}
        self.rediriger("/espace?nouveau=" + identifiant, set_cookie=f"sid={sid}; HttpOnly; Path=/; SameSite=Lax")

    def post_connexion_candidat(self):
        donnees = self.lire_formulaire()
        identifiant = self.champ(donnees, "identifiant").strip()
        mdp = self.champ(donnees, "motDePasse")
        conn = db.connexion()
        candidat = conn.execute("SELECT * FROM candidats WHERE identifiant = ?", (identifiant,)).fetchone()
        conn.close()
        if not candidat or not db.verifier_mot_de_passe(mdp, candidat["pass_salt"], candidat["pass_hash"]):
            return self.rediriger("/portail?erreur=" + "Identifiant ou mot de passe incorrect.")
        sid = secrets.token_hex(24)
        SESSIONS[sid] = {"kind": "candidat", "id": candidat["id"]}
        self.rediriger("/espace", set_cookie=f"sid={sid}; HttpOnly; Path=/; SameSite=Lax")

    def post_deconnexion(self):
        s = self.session()
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        if "sid" in cookie:
            SESSIONS.pop(cookie["sid"].value, None)
        self.rediriger("/portail", set_cookie="sid=; HttpOnly; Path=/; Max-Age=0")

    # ------------------------------------------------------------------
    # Espace candidat
    # ------------------------------------------------------------------
    def menu_espace(self, actif, profil=None):
        libelle_parcours = "Demande de parcours"
        if profil == "Étudiant":
            libelle_parcours = "Réinscription / Réorientation"
        elif profil == "Master Professionnel":
            libelle_parcours = "Demande d'inscription"
        dossier = [
            ("/espace", "Accueil", "tableau"),
            ("/espace/parcours", libelle_parcours, "parcours"),
            ("/espace/ue", "Unités d'enseignement", "ue"),
            ("/espace/paiement", "Paiement", "paiement"),
            ("/espace/fiche", "Fiche d'inscription", "fiche"),
            ("/espace/depot", "Dépôt définitif", "depot"),
        ]
        resultats = [
            ("/espace/resultats/notes", "Notes", "notes"),
            ("/espace/resultats/cursus", "Cursus", "cursus"),
            ("/espace/resultats/releves", "Relevés de notes", "releves"),
        ]
        oeuvres = [
            ("/espace/oeuvres/logement", "Demande de logement", "logement"),
            ("/espace/oeuvres/medicale", "Fiche d'analyse médicale", "medicale"),
            ("/espace/oeuvres/rdv", "Rendez-vous", "rdv"),
        ]

        def section(titre, liens):
            items = "".join(f'<a href="{h}" class="{"active" if actif == k else ""}">{esc(t)}</a>' for h, t, k in liens)
            return f'<div class="side-titre">{esc(titre)}</div>{items}'

        return section("Mon dossier", dossier) + section("Résultats", resultats) + section("Œuvres universitaires", oeuvres)

    LIBELLE_TYPE_DEMANDE = {
        "initiale": "Nouvelle inscription",
        "reinscription": "Réinscription",
        "reorientation": "Réorientation",
        "master": "Inscription Master Professionnel",
    }

    def vue_espace_tableau(self, qs=None):
        s = self.exiger("candidat")
        if not s:
            return
        conn = db.connexion()
        candidat = conn.execute("SELECT * FROM candidats WHERE id = ?", (s["id"],)).fetchone()
        demande = demande_de_candidat(conn, s["id"])
        voeu_ok = voeu_accorde_de_candidat(conn, s["id"])
        parcours_actuel = programme(conn, voeu_ok["program_id"])["name"] if voeu_ok else None
        ancien_parcours = programme(conn, candidat["ancien_parcours_id"]) if candidat["ancien_parcours_id"] else None
        lignes_demande = ""
        if demande:
            type_label = self.LIBELLE_TYPE_DEMANDE.get(demande["type"], demande["type"])
            for v in voeux_de_demande(conn, demande["id"]):
                p = programme(conn, v["program_id"])
                lignes_demande += f"<tr><td>{esc(type_label)}</td><td>{esc(p['name'])}</td><td>{badge(v['statut'])}</td></tr>"
        conn.close()
        banniere = ""
        if candidat["carte_disponible"]:
            banniere = '<div class="banniere">🪪 Votre carte d\'étudiant est disponible. Vous pouvez la retirer au service de la scolarité.</div>'
        ligne_ancien = f"<tr><td>Parcours précédent</td><td>{esc(ancien_parcours['name'])}</td></tr>" if ancien_parcours else ""
        ligne_actuel = f"<tr><td>Parcours actuel</td><td>{esc(parcours_actuel)}</td></tr>" if parcours_actuel else ""
        corps = f"""<div class="espace-wrap"><aside class="side">{self.menu_espace('tableau', candidat['profil'])}</aside><div>
        <h2>Tableau de bord</h2>{banniere}
        <div class="card"><h3>Informations identitaires</h3><table class="fiche">
          <tr><td>Nom</td><td>{esc(candidat['nom'])} {esc(candidat['prenom'])}</td></tr>
          <tr><td>Date de naissance</td><td>{esc(candidat['naissance'])}</td></tr>
          <tr><td>Identifiant</td><td>{esc(candidat['identifiant'])}</td></tr>
          <tr><td>Profil</td><td>{esc(candidat['profil'])}</td></tr>
        </table></div>
        <div class="card"><h3>Parcours actuels</h3><table class="fiche">{ligne_ancien}{ligne_actuel}</table>
        {('<p class="muted">Aucun parcours accordé pour le moment.</p>' if not (ligne_ancien or ligne_actuel) else '')}</div>
        <div class="card"><h3>Dernières inscriptions</h3>{table(['Type', 'Parcours', 'Statut'], lignes_demande, "Aucune demande d'inscription en cours.")}</div>
        </div></div>"""
        self.envoyer_html(page("Mon espace", corps, nav_candidat(), f"Connecté : {esc(candidat['prenom'])} {esc(candidat['nom'])}"))

    def vue_espace_parcours(self, qs=None):
        s = self.exiger("candidat")
        if not s:
            return
        conn = db.connexion()
        candidat = conn.execute("SELECT * FROM candidats WHERE id = ?", (s["id"],)).fetchone()
        profil = candidat["profil"]
        demande = demande_de_candidat(conn, s["id"])
        if demande:
            lignes = ""
            for v in voeux_de_demande(conn, demande["id"]):
                p = programme(conn, v["program_id"])
                lignes += f"<tr><td>{v['ordre']}</td><td>{esc(p['name'])}</td><td>{badge(v['statut'])}</td></tr>"
            conn.close()
            type_label = self.LIBELLE_TYPE_DEMANDE.get(demande["type"], demande["type"])
            contenu = f"""<h2>{esc(type_label)}</h2><div class="card"><h3>Votre demande</h3>{table(['Ordre', 'Parcours', 'Statut'], lignes)}</div>
            <p class="muted">La demande est traitée par le service de la scolarité sous quelques jours.</p>"""
        elif profil == "Étudiant":
            ancien = programme(conn, candidat["ancien_parcours_id"]) if candidat["ancien_parcours_id"] else None
            autres = conn.execute(
                "SELECT * FROM programs WHERE id != ? ORDER BY name", (candidat["ancien_parcours_id"] or 0,)
            ).fetchall()
            conn.close()
            options_autres = "".join(f'<option value="{p["id"]}">{esc(p["name"])}</option>' for p in autres)
            nom_ancien = ancien["name"] if ancien else "votre parcours précédent"
            contenu = f"""<h2>Réinscription / Réorientation</h2>
            <div class="card"><h3>Me réinscrire</h3><p>Continuer dans mon parcours actuel : <strong>{esc(nom_ancien)}</strong></p>
            <form method="post" action="/espace/parcours"><input type="hidden" name="action" value="reinscription">
            <button class="btn" type="submit">Confirmer ma réinscription</button></form></div>
            <div class="card"><h3>Demander une réorientation</h3><p class="muted">Choisissez un nouveau parcours ; la demande est soumise à validation par la scolarité.</p>
            <form class="form" method="post" action="/espace/parcours"><input type="hidden" name="action" value="reorientation">
              <select name="nouveauParcours" required><option value="">Nouveau parcours souhaité</option>{options_autres}</select>
              <button class="btn" type="submit">Envoyer la demande de réorientation</button>
            </form></div>"""
        elif profil == "Master Professionnel":
            progs = conn.execute("SELECT * FROM programs WHERE cycle = 'Master Professionnel' ORDER BY name").fetchall()
            conn.close()
            options = "".join(f'<option value="{p["id"]}">{esc(p["name"])}</option>' for p in progs)
            contenu = f"""<h2>Demande d'inscription — Master Professionnel</h2><div class="card">
            <p>Choisissez le parcours de Master Professionnel visé.</p>
            <form class="form" method="post" action="/espace/parcours">
              <select name="parcoursMaster" required><option value="">Parcours Master Professionnel</option>{options}</select>
              <button class="btn" type="submit">Envoyer la demande</button>
            </form></div>"""
        else:
            progs = conn.execute("SELECT * FROM programs WHERE cycle != 'Master Professionnel' ORDER BY name").fetchall()
            conn.close()
            options = lambda lbl: f'<option value="">{esc(lbl)}</option>' + "".join(f'<option value="{p["id"]}">{esc(p["name"])}</option>' for p in progs)
            contenu = f"""<h2>Demande de parcours</h2><div class="card">
            <p>Choisissez jusqu'à 3 parcours par ordre de préférence.</p>
            <form class="form" method="post" action="/espace/parcours">
              <select name="voeu1" required>{options('Parcours 1 (obligatoire)')}</select>
              <select name="voeu2">{options('Parcours 2 (optionnel)')}</select>
              <select name="voeu3">{options('Parcours 3 (optionnel)')}</select>
              <button class="btn" type="submit">Envoyer la demande</button>
            </form></div>"""
        corps = f'<div class="espace-wrap"><aside class="side">{self.menu_espace("parcours", profil)}</aside><div>{contenu}</div></div>'
        self.envoyer_html(page("Demande de parcours", corps, nav_candidat()))

    def post_espace_parcours(self):
        s = self.exiger("candidat")
        if not s:
            return
        donnees = self.lire_formulaire()
        conn = db.connexion()
        candidat = conn.execute("SELECT * FROM candidats WHERE id = ?", (s["id"],)).fetchone()
        profil = candidat["profil"]

        if profil == "Étudiant":
            action = self.champ(donnees, "action")
            if action == "reinscription" and candidat["ancien_parcours_id"]:
                cur = conn.execute("INSERT INTO demandes (candidat_id, type) VALUES (?, 'reinscription')", (s["id"],))
                conn.execute(
                    "INSERT INTO voeux (demande_id, program_id, ordre, statut) VALUES (?, ?, 1, 'En attente')",
                    (cur.lastrowid, candidat["ancien_parcours_id"]),
                )
            elif action == "reorientation" and self.champ(donnees, "nouveauParcours"):
                cur = conn.execute("INSERT INTO demandes (candidat_id, type) VALUES (?, 'reorientation')", (s["id"],))
                conn.execute(
                    "INSERT INTO voeux (demande_id, program_id, ordre, statut) VALUES (?, ?, 1, 'En attente')",
                    (cur.lastrowid, int(self.champ(donnees, "nouveauParcours"))),
                )
        elif profil == "Master Professionnel":
            parcours_master = self.champ(donnees, "parcoursMaster")
            if parcours_master:
                cur = conn.execute("INSERT INTO demandes (candidat_id, type) VALUES (?, 'master')", (s["id"],))
                conn.execute(
                    "INSERT INTO voeux (demande_id, program_id, ordre, statut) VALUES (?, ?, 1, 'En attente')",
                    (cur.lastrowid, int(parcours_master)),
                )
        else:
            voeux = [self.champ(donnees, f"voeu{i}") for i in (1, 2, 3)]
            voeux = [v for v in voeux if v]
            if voeux:
                cur = conn.execute("INSERT INTO demandes (candidat_id, type) VALUES (?, 'initiale')", (s["id"],))
                demande_id = cur.lastrowid
                for i, programme_id in enumerate(voeux):
                    conn.execute(
                        "INSERT INTO voeux (demande_id, program_id, ordre, statut) VALUES (?, ?, ?, 'En attente')",
                        (demande_id, int(programme_id), i + 1),
                    )
        conn.commit()
        conn.close()
        self.rediriger("/espace/parcours")

    def vue_espace_ue(self, qs=None):
        s = self.exiger("candidat")
        if not s:
            return
        qs = qs or {}
        onglet = (qs.get("onglet") or ["obligatoires"])[0]
        conn = db.connexion()
        candidat = conn.execute("SELECT * FROM candidats WHERE id = ?", (s["id"],)).fetchone()
        voeu_ok = voeu_accorde_de_candidat(conn, s["id"])
        if not voeu_ok:
            conn.close()
            contenu = "<h2>Unités d'enseignement</h2><p class=\"muted\">Votre parcours doit d'abord être accordé.</p>"
        else:
            prog = programme(conn, voeu_ok["program_id"])
            obligatoires = conn.execute(
                "SELECT * FROM ue_catalogue WHERE program_id = ? AND obligatoire = 1 ORDER BY code", (prog["id"],)
            ).fetchall()
            libres = conn.execute(
                "SELECT * FROM ue_catalogue WHERE program_id != ? AND obligatoire = 0 ORDER BY code", (prog["id"],)
            ).fetchall()
            insc = inscription_de_candidat(conn, s["id"])
            if insc:
                choisies_ids = {r["ue_id"] for r in conn.execute("SELECT ue_id FROM inscriptions_ue_items WHERE inscription_id = ?", (insc["id"],)).fetchall()}
                confirmees = bool(insc["confirmees"])
            else:
                choisies_ids = set()
                confirmees = False
            conn.close()

            libres_choisies = [u for u in libres if u["id"] in choisies_ids]
            toutes_choisies = list(obligatoires) + libres_choisies

            tabs = [
                ("obligatoires", "UE obligatoires"),
                ("libres", "UE libres"),
                ("desinscription", "Désinscription"),
                ("choisies", "UE choisies"),
                ("confirmees", "UE confirmées"),
            ]
            onglets_nav = '<div class="onglets">' + "".join(
                f'<a href="/espace/ue?onglet={k}" class="{"actif" if onglet == k else ""}">{esc(t)}</a>' for k, t in tabs
            ) + "</div>"

            if onglet == "libres":
                lignes = ""
                for u in libres:
                    coche = "checked" if u["id"] in choisies_ids else ""
                    dis = "disabled" if confirmees else ""
                    lignes += f"<tr><td><input type=\"checkbox\" name=\"ue\" value=\"{u['id']}\" {coche} {dis}></td><td>{esc(u['code'])}</td><td>{esc(u['libelle'])}</td><td>{u['credit']}</td></tr>"
                action = (
                    '<p class="muted">UE confirmées après paiement — modification impossible.</p>'
                    if confirmees else '<button class="btn" type="submit">Ajouter aux UE choisies</button>'
                )
                bloc = f'<form method="post" action="/espace/ue"><div class="card"><h3>Choix d\'UE libres (optionnelles, autres parcours)</h3>{table(["", "Code", "Libellé", "Crédit"], lignes)}{action}</div></form>'
            elif onglet == "desinscription":
                lignes = ""
                for u in libres_choisies:
                    bouton = "" if confirmees else (
                        f'<form method="post" action="/espace/ue/retirer" style="display:inline">'
                        f'<input type="hidden" name="ueId" value="{u["id"]}">'
                        f'<button class="btn danger" type="submit">Se désinscrire</button></form>'
                    )
                    lignes += f"<tr><td>{esc(u['code'])}</td><td>{esc(u['libelle'])}</td><td>{u['credit']}</td><td>{bouton}</td></tr>"
                bloc = f'<div class="card"><h3>Désinscription d\'UE libres</h3>{table(["Code", "Libellé", "Crédit", "Action"], lignes, "Aucune UE libre choisie pour le moment.")}</div>'
            elif onglet == "choisies":
                lignes = "".join(f"<tr><td>{esc(u['code'])}</td><td>{esc(u['libelle'])}</td><td>{u['credit']}</td></tr>" for u in toutes_choisies)
                bloc = f'<div class="card"><h3>UE choisies ({len(toutes_choisies)})</h3>{table(["Code", "Libellé", "Crédit"], lignes)}</div>'
            elif onglet == "confirmees":
                if confirmees:
                    lignes = "".join(f"<tr><td>{esc(u['code'])}</td><td>{esc(u['libelle'])}</td><td>{u['credit']}</td><td>{badge('Accordé')}</td></tr>" for u in toutes_choisies)
                    bloc = f'<div class="card"><h3>UE confirmées</h3>{table(["Code", "Libellé", "Crédit", "Statut"], lignes)}</div>'
                else:
                    bloc = '<div class="card"><p class="muted">Les UE seront confirmées automatiquement après le paiement de la scolarité.</p></div>'
            else:
                lignes = "".join(f"<tr><td>{esc(u['code'])}</td><td>{esc(u['libelle'])}</td><td>{u['credit']}</td><td>{badge('Accordé')}</td></tr>" for u in obligatoires)
                bloc = f'<div class="card"><h3>UE obligatoires — {esc(prog["name"])} (auto-inscrites)</h3>{table(["Code", "Libellé", "Crédit", "Statut"], lignes)}</div>'

            contenu = f"<h2>Unités d'enseignement — {esc(prog['name'])}</h2>{onglets_nav}{bloc}"
        corps = f'<div class="espace-wrap"><aside class="side">{self.menu_espace("ue", candidat["profil"])}</aside><div>{contenu}</div></div>'
        self.envoyer_html(page("Unités d'enseignement", corps, nav_candidat()))

    def post_espace_ue(self):
        s = self.exiger("candidat")
        if not s:
            return
        donnees = self.lire_formulaire()
        ue_ids_cochees = {int(x) for x in donnees.get("ue", [])}
        conn = db.connexion()
        voeu_ok = voeu_accorde_de_candidat(conn, s["id"])
        obligatoire_ids = set()
        if voeu_ok:
            obligatoire_ids = {
                r["id"] for r in conn.execute(
                    "SELECT id FROM ue_catalogue WHERE program_id = ? AND obligatoire = 1", (voeu_ok["program_id"],)
                ).fetchall()
            }
        insc = inscription_de_candidat(conn, s["id"])
        if not insc:
            cur = conn.execute("INSERT INTO inscriptions_ue (candidat_id) VALUES (?)", (s["id"],))
            insc_id = cur.lastrowid
            confirmees = False
        else:
            insc_id = insc["id"]
            confirmees = bool(insc["confirmees"])
        if not confirmees:
            existantes = {r["ue_id"] for r in conn.execute("SELECT ue_id FROM inscriptions_ue_items WHERE inscription_id = ?", (insc_id,)).fetchall()}
            a_ajouter = (obligatoire_ids | ue_ids_cochees) - existantes
            for ue_id in a_ajouter:
                conn.execute("INSERT INTO inscriptions_ue_items (inscription_id, ue_id) VALUES (?, ?)", (insc_id, ue_id))
        conn.commit()
        conn.close()
        self.rediriger("/espace/ue?onglet=libres")

    def post_espace_ue_retirer(self):
        s = self.exiger("candidat")
        if not s:
            return
        donnees = self.lire_formulaire()
        ue_id = int(self.champ(donnees, "ueId", "0") or 0)
        conn = db.connexion()
        insc = inscription_de_candidat(conn, s["id"])
        if insc and not insc["confirmees"] and ue_id:
            conn.execute(
                "DELETE FROM inscriptions_ue_items WHERE inscription_id = ? AND ue_id = ? AND ue_id NOT IN (SELECT id FROM ue_catalogue WHERE obligatoire = 1)",
                (insc["id"], ue_id),
            )
            conn.commit()
        conn.close()
        self.rediriger("/espace/ue?onglet=desinscription")

    def vue_espace_paiement(self, qs=None):
        s = self.exiger("candidat")
        if not s:
            return
        conn = db.connexion()
        candidat = conn.execute("SELECT * FROM candidats WHERE id = ?", (s["id"],)).fetchone()
        paiement = paiement_de_candidat(conn, s["id"])
        conn.close()
        if paiement:
            detail = f"<p>Quittance : <strong>{esc(paiement['quittance'])}</strong> — payée le {esc(paiement['date'])}.</p>"
            statut = '<span class="badge badge-accorde">Payé</span>'
        else:
            detail = '<form method="post" action="/espace/paiement"><button class="btn" type="submit">Enregistrer le paiement</button></form><p class="muted">Prototype : aucun partenaire de paiement réel n\'est connecté ici.</p>'
            statut = '<span class="badge badge-attente">Non payé</span>'
        contenu = f"""<h2>Paiement de la scolarité</h2><div class="card"><h3>Frais dus</h3>
        <table class="fiche"><tr><td>Montant</td><td>{fmt_fcfa(MONTANT_SCOLARITE)}</td></tr><tr><td>Statut</td><td>{statut}</td></tr></table>
        {detail}</div>"""
        corps = f'<div class="espace-wrap"><aside class="side">{self.menu_espace("paiement", candidat["profil"])}</aside><div>{contenu}</div></div>'
        self.envoyer_html(page("Paiement", corps, nav_candidat()))

    def post_espace_paiement(self):
        s = self.exiger("candidat")
        if not s:
            return
        conn = db.connexion()
        if not paiement_de_candidat(conn, s["id"]):
            quittance = "QT-" + str(int(time.time() * 1000))
            conn.execute("INSERT INTO paiements (candidat_id, montant, quittance) VALUES (?, ?, ?)", (s["id"], MONTANT_SCOLARITE, quittance))
            insc = inscription_de_candidat(conn, s["id"])
            if insc:
                conn.execute("UPDATE inscriptions_ue SET confirmees = 1 WHERE id = ?", (insc["id"],))
            conn.commit()
        conn.close()
        self.rediriger("/espace/paiement")

    def vue_espace_fiche(self, qs=None):
        s = self.exiger("candidat")
        if not s:
            return
        conn = db.connexion()
        candidat = conn.execute("SELECT * FROM candidats WHERE id = ?", (s["id"],)).fetchone()
        paiement = paiement_de_candidat(conn, s["id"])
        voeu_ok = voeu_accorde_de_candidat(conn, s["id"])
        if not paiement or not voeu_ok:
            conn.close()
            contenu = "<h2>Fiche d'inscription</h2><p class=\"muted\">Disponible une fois le paiement effectué.</p>"
        else:
            prog = programme(conn, voeu_ok["program_id"])
            conn.close()
            contenu = f"""<h2>Fiche d'inscription</h2><div class="fiche"><h3>HTIB ATLANTIS — Fiche d'inscription définitive</h3><table>
            <tr><td>Étudiant</td><td>{esc(candidat['nom'])} {esc(candidat['prenom'])}</td></tr>
            <tr><td>Parcours</td><td>{esc(prog['name'])} — {esc(prog['cycle'])}</td></tr>
            <tr><td>Année académique</td><td>2026-2027</td></tr>
            <tr><td>Quittance</td><td>{esc(paiement['quittance'])}</td></tr>
            <tr><td>Montant payé</td><td>{fmt_fcfa(paiement['montant'])}</td></tr>
            </table><p class="muted">Imprimez cette page avec la fonction d'impression de votre navigateur (Ctrl+P).</p></div>"""
        corps = f'<div class="espace-wrap"><aside class="side">{self.menu_espace("fiche", candidat["profil"])}</aside><div>{contenu}</div></div>'
        self.envoyer_html(page("Fiche d'inscription", corps, nav_candidat()))

    def vue_espace_depot(self, qs=None):
        s = self.exiger("candidat")
        if not s:
            return
        conn = db.connexion()
        candidat = conn.execute("SELECT * FROM candidats WHERE id = ?", (s["id"],)).fetchone()
        demande = demande_de_candidat(conn, s["id"])
        conn.close()
        profil = candidat["profil"]
        type_demande = demande["type"] if demande else None

        items = [
            "Fiche d'inscription définitive (2 exemplaires)",
            "Fiche d'unités d'enseignement (3 exemplaires)",
            "Bordereau/quittance de paiement (original + 2 copies)",
            "Acte de naissance (copie légalisée)",
            "Pièce d'identité ou nationalité (copie légalisée)",
            "Photos d'identité (2 exemplaires)",
        ]
        if type_demande in ("reinscription", "reorientation"):
            items.insert(4, "Certificat de scolarité de l'année précédente")
            if type_demande == "reorientation":
                items.append("Lettre de motivation de réorientation")
        elif profil == "Master Professionnel":
            items.insert(4, "Diplôme de Licence ou BTS (copie légalisée)")
            items.append("Attestation de stage ou d'expérience professionnelle (si applicable)")
        else:
            items.insert(4, "Diplôme ou attestation du BAC (copie légalisée)")
            items.append("Relevés de notes / bulletins (copies)")
            items.append("Autorisation parentale (si mineur à la date du dépôt)")
        if profil == "BAC étranger":
            items.append("Attestation d'équivalence du diplôme (légalisation consulaire)")
            items.append("Copie légalisée du diplôme obtenu à l'étranger")

        liste = "".join(f"<li>☐ {esc(i)}</li>" for i in items)
        contenu = f'<h2>Dépôt définitif du dossier</h2><div class="card"><p>Documents à apporter, en original et copies, au service de la scolarité :</p><ul class="checklist">{liste}</ul></div>'
        corps = f'<div class="espace-wrap"><aside class="side">{self.menu_espace("depot", profil)}</aside><div>{contenu}</div></div>'
        self.envoyer_html(page("Dépôt définitif", corps, nav_candidat()))

    # ------------------------------------------------------------------
    # Résultats (Notes, Cursus, Relevés de notes)
    # ------------------------------------------------------------------
    def vue_resultats_notes(self, qs=None):
        s = self.exiger("candidat")
        if not s:
            return
        conn = db.connexion()
        candidat = conn.execute("SELECT * FROM candidats WHERE id = ?", (s["id"],)).fetchone()
        insc = inscription_de_candidat(conn, s["id"])
        lignes = ""
        if insc:
            ues = conn.execute(
                """SELECT u.* FROM ue_catalogue u JOIN inscriptions_ue_items i ON u.id = i.ue_id
                   WHERE i.inscription_id = ? ORDER BY u.code""",
                (insc["id"],),
            ).fetchall()
            for u in ues:
                lignes += f"<tr><td>{esc(u['code'])}</td><td>{esc(u['libelle'])}</td><td class=\"muted\">Non disponible</td></tr>"
        conn.close()
        contenu = f"""<h2>Notes</h2><div class="card">{table(['Code UE', 'Libellé', 'Note'], lignes, "Aucune UE inscrite pour le moment.")}</div>
        <p class="muted">Les notes sont publiées par le service de la scolarité après chaque session d'examens.</p>"""
        corps = f'<div class="espace-wrap"><aside class="side">{self.menu_espace("notes", candidat["profil"])}</aside><div>{contenu}</div></div>'
        self.envoyer_html(page("Notes", corps, nav_candidat()))

    def vue_resultats_cursus(self, qs=None):
        s = self.exiger("candidat")
        if not s:
            return
        conn = db.connexion()
        candidat = conn.execute("SELECT * FROM candidats WHERE id = ?", (s["id"],)).fetchone()
        lignes = ""
        for d in conn.execute("SELECT * FROM demandes WHERE candidat_id = ? ORDER BY id", (s["id"],)).fetchall():
            type_label = self.LIBELLE_TYPE_DEMANDE.get(d["type"], d["type"])
            for v in voeux_de_demande(conn, d["id"]):
                p = programme(conn, v["program_id"])
                lignes += f"<tr><td>{esc(d['created_at'])}</td><td>{esc(type_label)}</td><td>{esc(p['name'])}</td><td>{badge(v['statut'])}</td></tr>"
        conn.close()
        contenu = f"""<h2>Cursus</h2><div class="card"><h3>Historique des parcours</h3>
        {table(['Date', 'Type de demande', 'Parcours', 'Statut'], lignes, "Aucun historique pour le moment.")}</div>"""
        corps = f'<div class="espace-wrap"><aside class="side">{self.menu_espace("cursus", candidat["profil"])}</aside><div>{contenu}</div></div>'
        self.envoyer_html(page("Cursus", corps, nav_candidat()))

    def vue_resultats_releves(self, qs=None):
        s = self.exiger("candidat")
        if not s:
            return
        conn = db.connexion()
        candidat = conn.execute("SELECT * FROM candidats WHERE id = ?", (s["id"],)).fetchone()
        conn.close()
        contenu = """<h2>Relevés de notes</h2><div class="card">
        <p class="muted">Aucun relevé de notes disponible pour le moment. Les relevés officiels sont délivrés par le
        service de la scolarité après la proclamation des résultats de fin de semestre.</p></div>"""
        corps = f'<div class="espace-wrap"><aside class="side">{self.menu_espace("releves", candidat["profil"])}</aside><div>{contenu}</div></div>'
        self.envoyer_html(page("Relevés de notes", corps, nav_candidat()))

    # ------------------------------------------------------------------
    # Œuvres universitaires (logement, fiche médicale, rendez-vous)
    # ------------------------------------------------------------------
    def vue_oeuvres_logement(self, qs=None):
        s = self.exiger("candidat")
        if not s:
            return
        conn = db.connexion()
        candidat = conn.execute("SELECT * FROM candidats WHERE id = ?", (s["id"],)).fetchone()
        demande_log = logement_de_candidat(conn, s["id"])
        conn.close()
        if demande_log:
            contenu = f"""<h2>Demande de logement</h2><div class="card"><table class="fiche">
            <tr><td>Type demandé</td><td>{esc(demande_log['type_logement'])}</td></tr>
            <tr><td>Statut</td><td>{badge(demande_log['statut'])}</td></tr>
            </table></div>"""
        else:
            contenu = """<h2>Demande de logement</h2><div class="card">
            <form class="form" method="post" action="/espace/oeuvres/logement">
              <select name="typeLogement" required><option value="">Type de logement souhaité</option>
                <option>Chambre individuelle</option><option>Chambre partagée</option><option>Pas de logement requis</option>
              </select>
              <button class="btn" type="submit">Envoyer la demande</button>
            </form></div>"""
        corps = f'<div class="espace-wrap"><aside class="side">{self.menu_espace("logement", candidat["profil"])}</aside><div>{contenu}</div></div>'
        self.envoyer_html(page("Demande de logement", corps, nav_candidat()))

    def post_oeuvres_logement(self):
        s = self.exiger("candidat")
        if not s:
            return
        donnees = self.lire_formulaire()
        type_logement = self.champ(donnees, "typeLogement")
        conn = db.connexion()
        if type_logement and not logement_de_candidat(conn, s["id"]):
            conn.execute(
                "INSERT INTO demandes_logement (candidat_id, type_logement) VALUES (?, ?)", (s["id"], type_logement)
            )
            conn.commit()
        conn.close()
        self.rediriger("/espace/oeuvres/logement")

    def vue_oeuvres_medicale(self, qs=None):
        s = self.exiger("candidat")
        if not s:
            return
        conn = db.connexion()
        candidat = conn.execute("SELECT * FROM candidats WHERE id = ?", (s["id"],)).fetchone()
        analyse = medicale_de_candidat(conn, s["id"])
        conn.close()
        montant = 2000
        if analyse and analyse["paye"]:
            detail = f"<p>Quittance : <strong>{esc(analyse['quittance'])}</strong>.</p><p class=\"muted\">Présentez-vous à l'infirmerie avec cette quittance pour la visite médicale d'entrée.</p>"
            statut = '<span class="badge badge-accorde">Payé</span>'
        else:
            detail = '<form method="post" action="/espace/oeuvres/medicale"><button class="btn" type="submit">Payer les frais d\'analyse</button></form>'
            statut = '<span class="badge badge-attente">Non payé</span>'
        contenu = f"""<h2>Fiche d'analyse médicale</h2><div class="card"><h3>Frais d'analyse médicale d'entrée</h3>
        <table class="fiche"><tr><td>Montant</td><td>{fmt_fcfa(montant)}</td></tr><tr><td>Statut</td><td>{statut}</td></tr></table>
        {detail}</div>"""
        corps = f'<div class="espace-wrap"><aside class="side">{self.menu_espace("medicale", candidat["profil"])}</aside><div>{contenu}</div></div>'
        self.envoyer_html(page("Fiche d'analyse médicale", corps, nav_candidat()))

    def post_oeuvres_medicale(self):
        s = self.exiger("candidat")
        if not s:
            return
        conn = db.connexion()
        if not medicale_de_candidat(conn, s["id"]):
            quittance = "QM-" + str(int(time.time() * 1000))
            conn.execute(
                "INSERT INTO analyses_medicales (candidat_id, paye, quittance) VALUES (?, 1, ?)", (s["id"], quittance)
            )
            conn.commit()
        conn.close()
        self.rediriger("/espace/oeuvres/medicale")

    def vue_oeuvres_rdv(self, qs=None):
        s = self.exiger("candidat")
        if not s:
            return
        conn = db.connexion()
        candidat = conn.execute("SELECT * FROM candidats WHERE id = ?", (s["id"],)).fetchone()
        rdvs = rdv_de_candidat(conn, s["id"])
        conn.close()
        lignes = "".join(f"<tr><td>{esc(r['motif'])}</td><td>{esc(r['date_souhaitee'])}</td><td>{badge(r['statut'])}</td></tr>" for r in rdvs)
        contenu = f"""<h2>Rendez-vous</h2>
        <div class="card"><form class="form" method="post" action="/espace/oeuvres/rdv">
          <input name="motif" placeholder="Motif du rendez-vous" required>
          <input name="dateSouhaitee" type="date" required>
          <button class="btn" type="submit">Demander un rendez-vous</button>
        </form></div>
        <div class="card"><h3>Mes demandes</h3>{table(['Motif', 'Date souhaitée', 'Statut'], lignes, "Aucune demande de rendez-vous.")}</div>"""
        corps = f'<div class="espace-wrap"><aside class="side">{self.menu_espace("rdv", candidat["profil"])}</aside><div>{contenu}</div></div>'
        self.envoyer_html(page("Rendez-vous", corps, nav_candidat()))

    def post_oeuvres_rdv(self):
        s = self.exiger("candidat")
        if not s:
            return
        donnees = self.lire_formulaire()
        motif = self.champ(donnees, "motif").strip()
        date_s = self.champ(donnees, "dateSouhaitee")
        if motif:
            conn = db.connexion()
            conn.execute(
                "INSERT INTO rendezvous (candidat_id, motif, date_souhaitee) VALUES (?, ?, ?)", (s["id"], motif, date_s)
            )
            conn.commit()
            conn.close()
        self.rediriger("/espace/oeuvres/rdv")

    # ------------------------------------------------------------------
    # Espace personnel
    # ------------------------------------------------------------------
    def vue_staff_connexion(self, qs=None):
        qs = qs or {}
        erreur = (qs.get("erreur") or [""])[0]
        corps = f"""<div class="fiche" style="max-width:420px;margin:0 auto">
        <h2>Espace personnel</h2><p class="muted">Réservé au service de la scolarité.</p>
        {erreur_box(erreur)}
        <form class="form" method="post" action="/personnel/connexion">
          <input name="identifiant" placeholder="Identifiant" required>
          <input name="motDePasse" type="password" placeholder="Mot de passe" required>
          <button class="btn" type="submit">Connexion</button>
        </form>
        <p><a href="/portail">← Retour au portail</a></p>
        </div>"""
        self.envoyer_html(page("Espace personnel", corps, nav_visiteur()))

    def post_staff_connexion(self):
        donnees = self.lire_formulaire()
        identifiant = self.champ(donnees, "identifiant").strip()
        mdp = self.champ(donnees, "motDePasse")
        conn = db.connexion()
        staff = conn.execute("SELECT * FROM staff WHERE identifiant = ?", (identifiant,)).fetchone()
        conn.close()
        if not staff or not db.verifier_mot_de_passe(mdp, staff["pass_salt"], staff["pass_hash"]):
            return self.rediriger("/personnel/connexion?erreur=Identifiant ou mot de passe incorrect.")
        sid = secrets.token_hex(24)
        SESSIONS[sid] = {"kind": "staff", "id": staff["id"]}
        self.rediriger("/personnel/tableau", set_cookie=f"sid={sid}; HttpOnly; Path=/; SameSite=Lax")

    def vue_staff_tableau(self, qs=None):
        s = self.exiger("staff")
        if not s:
            return
        conn = db.connexion()
        nb_students = conn.execute("SELECT COUNT(*) AS n FROM students").fetchone()["n"]
        nb_demandes = conn.execute("SELECT COUNT(*) AS n FROM demandes").fetchone()["n"]
        nb_progs = conn.execute("SELECT COUNT(*) AS n FROM programs").fetchone()["n"]
        total_paiements = conn.execute("SELECT COALESCE(SUM(montant),0) AS t FROM paiements").fetchone()["t"]
        lignes_types = ""
        for type_demande, libelle in self.LIBELLE_TYPE_DEMANDE.items():
            n = conn.execute("SELECT COUNT(*) AS n FROM demandes WHERE type = ?", (type_demande,)).fetchone()["n"]
            lignes_types += f"<tr><td>{esc(libelle)}</td><td>{n}</td></tr>"
        nb_attente = conn.execute("SELECT COUNT(*) AS n FROM voeux WHERE statut = 'En attente'").fetchone()["n"]
        nb_logement = conn.execute("SELECT COUNT(*) AS n FROM demandes_logement WHERE statut = 'En attente'").fetchone()["n"]
        nb_rdv = conn.execute("SELECT COUNT(*) AS n FROM rendezvous WHERE statut = 'Demandé'").fetchone()["n"]
        conn.close()
        contenu = f"""<h2>Tableau de bord</h2><div class="cards">
        <div class="card">Étudiants<div class="num">{nb_students}</div></div>
        <div class="card">Candidatures<div class="num">{nb_demandes}</div></div>
        <div class="card">Parcours<div class="num">{nb_progs}</div></div>
        <div class="card">Paiements<div class="num">{fmt_fcfa(total_paiements)}</div></div>
        </div>
        <div class="cards">
        <div class="card">Vœux en attente<div class="num">{nb_attente}</div></div>
        <div class="card">Demandes de logement en attente<div class="num">{nb_logement}</div></div>
        <div class="card">Rendez-vous demandés<div class="num">{nb_rdv}</div></div>
        </div>
        <div class="card"><h3>Candidatures par type</h3>{table(['Type de demande', 'Nombre'], lignes_types)}</div>
        <div class="card"><h3>Structure</h3><p>Établissement → Parcours → Candidature (initiale, réinscription, réorientation, Master Pro) → Unités d'enseignement → Étudiant</p>
        <p class="muted">Les données sont enregistrées dans une vraie base SQLite côté serveur, partagée par tout le personnel connecté.</p></div>"""
        self.envoyer_html(page("Tableau de bord", contenu, nav_staff("tableau")))

    def vue_staff_validation(self, qs=None):
        s = self.exiger("staff")
        if not s:
            return
        conn = db.connexion()
        demandes = conn.execute("SELECT * FROM demandes ORDER BY id DESC").fetchall()
        lignes_demandes = ""
        for d in demandes:
            candidat = conn.execute("SELECT * FROM candidats WHERE id = ?", (d["candidat_id"],)).fetchone()
            type_label = self.LIBELLE_TYPE_DEMANDE.get(d["type"], d["type"])
            for v in voeux_de_demande(conn, d["id"]):
                p = programme(conn, v["program_id"])
                action = ""
                if v["statut"] == "En attente":
                    action = (
                        f'<form method="post" action="/personnel/voeu-statut" style="display:inline">'
                        f'<input type="hidden" name="voeuId" value="{v["id"]}"><input type="hidden" name="statut" value="Accordé">'
                        f'<button class="btn" type="submit">Accorder</button></form> '
                        f'<form method="post" action="/personnel/voeu-statut" style="display:inline">'
                        f'<input type="hidden" name="voeuId" value="{v["id"]}"><input type="hidden" name="statut" value="Rejeté">'
                        f'<button class="btn danger" type="submit">Rejeter</button></form>'
                    )
                lignes_demandes += f"<tr><td>{esc(candidat['nom'])} {esc(candidat['prenom'])}</td><td>{esc(type_label)}</td><td>{v['ordre']}</td><td>{esc(p['name'])}</td><td>{badge(v['statut'])}</td><td>{action}</td></tr>"

        candidats = conn.execute("SELECT * FROM candidats ORDER BY id DESC").fetchall()
        lignes_cartes = ""
        for c in candidats:
            voeu_ok = voeu_accorde_de_candidat(conn, c["id"])
            paie = paiement_de_candidat(conn, c["id"])
            if c["carte_disponible"]:
                carte = '<span class="badge badge-accorde">Disponible</span>'
            elif voeu_ok and paie:
                carte = f'<form method="post" action="/personnel/carte" style="display:inline"><input type="hidden" name="candidatId" value="{c["id"]}"><button class="btn" type="submit">Marquer disponible</button></form>'
            else:
                carte = '<span class="muted">—</span>'
            prog_nom = programme(conn, voeu_ok["program_id"])["name"] if voeu_ok else '<span class="muted">—</span>'
            lignes_cartes += f"<tr><td>{esc(c['nom'])} {esc(c['prenom'])}</td><td>{esc(prog_nom) if voeu_ok else prog_nom}</td><td>{'Oui' if paie else 'Non'}</td><td>{carte}</td></tr>"
        conn.close()

        contenu = f"""<h2>Validation des candidatures</h2>
        <div class="card"><h3>Demandes de parcours</h3>{table(['Candidat', 'Type', 'Ordre', 'Parcours', 'Statut', 'Action'], lignes_demandes, 'Aucune candidature pour le moment.')}</div>
        <div class="card"><h3>Cartes d'étudiant</h3>{table(['Candidat', 'Parcours accordé', 'Payé', 'Carte'], lignes_cartes, 'Aucun candidat pour le moment.')}</div>"""
        self.envoyer_html(page("Validation", contenu, nav_staff("validation")))

    def post_voeu_statut(self):
        s = self.exiger("staff")
        if not s:
            return
        donnees = self.lire_formulaire()
        voeu_id = int(self.champ(donnees, "voeuId"))
        statut = self.champ(donnees, "statut")
        if statut in ("Accordé", "Rejeté", "En attente"):
            conn = db.connexion()
            conn.execute("UPDATE voeux SET statut = ? WHERE id = ?", (statut, voeu_id))
            conn.commit()
            conn.close()
        self.rediriger("/personnel/validation")

    def post_carte(self):
        s = self.exiger("staff")
        if not s:
            return
        donnees = self.lire_formulaire()
        candidat_id = int(self.champ(donnees, "candidatId"))
        conn = db.connexion()
        conn.execute("UPDATE candidats SET carte_disponible = 1 WHERE id = ?", (candidat_id,))
        conn.commit()
        conn.close()
        self.rediriger("/personnel/validation")

    def vue_staff_etudiants(self, qs=None):
        s = self.exiger("staff")
        if not s:
            return
        conn = db.connexion()
        etudiants = conn.execute("SELECT * FROM students ORDER BY id DESC").fetchall()
        conn.close()
        lignes = "".join(f"<tr><td>{esc(e['nom'])}</td><td>{esc(e['matricule'])}</td><td>{esc(e['parcours'])}</td><td>{esc(e['niveau'])}</td></tr>" for e in etudiants)
        contenu = f"""<h2>Étudiants</h2>
        <div class="card"><form class="form" method="post" action="/personnel/etudiants">
          <input name="nom" placeholder="Nom complet" required>
          <input name="matricule" placeholder="Matricule">
          <input name="parcours" placeholder="Parcours">
          <input name="niveau" placeholder="Niveau">
          <button class="btn" type="submit">Enregistrer</button>
        </form></div>
        <div class="card">{table(['Nom', 'Matricule', 'Parcours', 'Niveau'], lignes)}</div>"""
        self.envoyer_html(page("Étudiants", contenu, nav_staff("etudiants")))

    def post_staff_etudiants(self):
        s = self.exiger("staff")
        if not s:
            return
        donnees = self.lire_formulaire()
        nom = self.champ(donnees, "nom").strip()
        if nom:
            conn = db.connexion()
            conn.execute(
                "INSERT INTO students (nom, matricule, parcours, niveau) VALUES (?, ?, ?, ?)",
                (nom, self.champ(donnees, "matricule"), self.champ(donnees, "parcours"), self.champ(donnees, "niveau")),
            )
            conn.commit()
            conn.close()
        self.rediriger("/personnel/etudiants")

    def vue_staff_paiements(self, qs=None):
        s = self.exiger("staff")
        if not s:
            return
        conn = db.connexion()
        paiements = conn.execute(
            "SELECT p.*, c.nom, c.prenom FROM paiements p JOIN candidats c ON p.candidat_id = c.id ORDER BY p.id DESC"
        ).fetchall()
        conn.close()
        lignes = "".join(f"<tr><td>{esc(p['date'])}</td><td>{esc(p['nom'])} {esc(p['prenom'])}</td><td>{fmt_fcfa(p['montant'])}</td><td>{esc(p['quittance'])}</td></tr>" for p in paiements)
        contenu = f"<h2>Paiements</h2><div class=\"card\">{table(['Date', 'Candidat', 'Montant', 'Quittance'], lignes)}</div>"
        self.envoyer_html(page("Paiements", contenu, nav_staff("paiements")))

    def vue_staff_oeuvres(self, qs=None):
        s = self.exiger("staff")
        if not s:
            return
        conn = db.connexion()
        logements = conn.execute(
            "SELECT l.*, c.nom, c.prenom FROM demandes_logement l JOIN candidats c ON l.candidat_id = c.id ORDER BY l.id DESC"
        ).fetchall()
        lignes_logement = ""
        for l in logements:
            action = ""
            if l["statut"] == "En attente":
                action = (
                    f'<form method="post" action="/personnel/logement-statut" style="display:inline">'
                    f'<input type="hidden" name="logementId" value="{l["id"]}"><input type="hidden" name="statut" value="Accordé">'
                    f'<button class="btn" type="submit">Accorder</button></form> '
                    f'<form method="post" action="/personnel/logement-statut" style="display:inline">'
                    f'<input type="hidden" name="logementId" value="{l["id"]}"><input type="hidden" name="statut" value="Rejeté">'
                    f'<button class="btn danger" type="submit">Rejeter</button></form>'
                )
            lignes_logement += f"<tr><td>{esc(l['nom'])} {esc(l['prenom'])}</td><td>{esc(l['type_logement'])}</td><td>{badge(l['statut'])}</td><td>{action}</td></tr>"

        rdvs = conn.execute(
            "SELECT r.*, c.nom, c.prenom FROM rendezvous r JOIN candidats c ON r.candidat_id = c.id ORDER BY r.id DESC"
        ).fetchall()
        lignes_rdv = ""
        for r in rdvs:
            action = ""
            if r["statut"] == "Demandé":
                action = (
                    f'<form method="post" action="/personnel/rdv-statut" style="display:inline">'
                    f'<input type="hidden" name="rdvId" value="{r["id"]}"><input type="hidden" name="statut" value="Confirmé">'
                    f'<button class="btn" type="submit">Confirmer</button></form> '
                    f'<form method="post" action="/personnel/rdv-statut" style="display:inline">'
                    f'<input type="hidden" name="rdvId" value="{r["id"]}"><input type="hidden" name="statut" value="Annulé">'
                    f'<button class="btn danger" type="submit">Annuler</button></form>'
                )
            lignes_rdv += f"<tr><td>{esc(r['nom'])} {esc(r['prenom'])}</td><td>{esc(r['motif'])}</td><td>{esc(r['date_souhaitee'])}</td><td>{badge(r['statut'])}</td><td>{action}</td></tr>"

        medicales = conn.execute(
            "SELECT m.*, c.nom, c.prenom FROM analyses_medicales m JOIN candidats c ON m.candidat_id = c.id WHERE m.paye = 1 ORDER BY m.id DESC"
        ).fetchall()
        lignes_medicale = "".join(
            f"<tr><td>{esc(m['nom'])} {esc(m['prenom'])}</td><td>{esc(m['quittance'])}</td><td>{esc(m['created_at'])}</td></tr>" for m in medicales
        )
        conn.close()

        contenu = f"""<h2>Œuvres universitaires</h2>
        <div class="card"><h3>Demandes de logement</h3>{table(['Candidat', 'Type', 'Statut', 'Action'], lignes_logement, 'Aucune demande.')}</div>
        <div class="card"><h3>Rendez-vous</h3>{table(['Candidat', 'Motif', 'Date souhaitée', 'Statut', 'Action'], lignes_rdv, 'Aucune demande.')}</div>
        <div class="card"><h3>Analyses médicales payées</h3>{table(['Candidat', 'Quittance', 'Date'], lignes_medicale, 'Aucun paiement.')}</div>"""
        self.envoyer_html(page("Œuvres universitaires", contenu, nav_staff("oeuvres")))

    def post_staff_logement_statut(self):
        s = self.exiger("staff")
        if not s:
            return
        donnees = self.lire_formulaire()
        logement_id = int(self.champ(donnees, "logementId", "0") or 0)
        statut = self.champ(donnees, "statut")
        if statut in ("Accordé", "Rejeté", "En attente") and logement_id:
            conn = db.connexion()
            conn.execute("UPDATE demandes_logement SET statut = ? WHERE id = ?", (statut, logement_id))
            conn.commit()
            conn.close()
        self.rediriger("/personnel/oeuvres")

    def post_staff_rdv_statut(self):
        s = self.exiger("staff")
        if not s:
            return
        donnees = self.lire_formulaire()
        rdv_id = int(self.champ(donnees, "rdvId", "0") or 0)
        statut = self.champ(donnees, "statut")
        if statut in ("Confirmé", "Annulé", "Demandé") and rdv_id:
            conn = db.connexion()
            conn.execute("UPDATE rendezvous SET statut = ? WHERE id = ?", (statut, rdv_id))
            conn.commit()
            conn.close()
        self.rediriger("/personnel/oeuvres")


def main():
    db.initialiser()
    serveur = ThreadingHTTPServer(("0.0.0.0", PORT), Gestionnaire)
    print(f"Portail HTIB ATLANTIS (100% Python, sans JavaScript) écoute sur le port {PORT}")
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
