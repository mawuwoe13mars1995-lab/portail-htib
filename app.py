#!/usr/bin/env python3
"""Portail HTIB ATLANTIS, serveur 100% Python (bibliothèque standard uniquement).

Aucun JavaScript : chaque action est un lien ou un formulaire HTML classique,
traité par le serveur qui renvoie une page complète (comme un site web
"à l'ancienne" — PHP, CGI — mais en Python). Pas de pip install nécessaire.

Lancement : python3 app.py   (ou "py app.py" sous Windows)
"""
import csv
import html
import io
import os
import re
import secrets
import textwrap
import time
import zipfile
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import urlparse, parse_qs, quote

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
# Génération du guide utilisateur en PDF — aucune bibliothèque externe :
# le fichier PDF est construit directement, octet par octet, à partir de la
# bibliothèque standard de Python uniquement.
# ----------------------------------------------------------------------------

SECTIONS_GUIDE = [
    ("1. Créer un compte", [
        "Depuis la page d'accueil du portail, choisissez votre profil (Nouveau bachelier, "
        "Ancien bachelier, Étudiant déjà inscrit, BAC étranger ou Master Professionnel), puis "
        "cliquez sur « Créer un compte ». Renseignez votre identité : un identifiant et un mot "
        "de passe vous seront demandés pour vous connecter ensuite.",
    ]),
    ("2. Déposer vos vœux de parcours", [
        "Une fois connecté, ouvrez « Mon espace » puis « Parcours ». Les nouveaux bacheliers et "
        "les bacheliers étrangers classent jusqu'à 3 vœux de parcours par ordre de préférence. "
        "Les étudiants déjà inscrits confirment leur réinscription dans leur parcours actuel ou "
        "déposent une demande de réorientation. Les candidats au Master Professionnel choisissent "
        "directement le parcours visé.",
    ]),
    ("3. Attendre la validation", [
        "Le service de la scolarité examine votre dossier et accorde ou rejette votre vœu. Le "
        "statut est visible à tout moment dans votre espace candidat.",
    ]),
    ("4. Unités d'enseignement", [
        "Une fois votre parcours accordé, rendez-vous dans « Unités d'enseignement ». Les UE "
        "obligatoires sont inscrites automatiquement ; vous choisissez vos UE libres, pouvez "
        "retirer une UE libre avant confirmation, et consultez le récapitulatif de vos UE "
        "choisies puis confirmées après paiement.",
    ]),
    ("5. Paiement et fiche d'inscription", [
        "Effectuez le paiement des frais de scolarité depuis votre espace, puis téléchargez "
        "votre fiche d'inscription.",
    ]),
    ("6. Dépôt du dossier", [
        "Déposez les pièces demandées (variable selon votre profil : diplôme du BAC, "
        "équivalence pour les bacheliers étrangers, diplôme de Licence ou BTS pour le Master "
        "Professionnel, etc.).",
    ]),
    ("7. Résultats et œuvres universitaires", [
        "Les menus « Résultats » (notes, cursus, relevés) et « Œuvres universitaires » "
        "(logement, fiche médicale, rendez-vous) sont accessibles depuis votre espace une fois "
        "votre dossier en cours de traitement.",
    ]),
]


def _pdf_echapper(texte):
    return texte.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_assembler(pages_textes):
    """Construit les octets d'un PDF valide à partir du contenu texte de
    chaque page (opérateurs de flux déjà prêts)."""
    objets = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        ("<< /Type /Pages /Kids [" + " ".join(f"{5 + 2 * i} 0 R" for i in range(len(pages_textes))) + f"] /Count {len(pages_textes)} >>").encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    for contenu in pages_textes:
        contenu_bytes = contenu.encode("cp1252", errors="replace")
        obj_contenu = len(objets) + 2
        objets.append(
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
             f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {obj_contenu} 0 R >>").encode("ascii")
        )
        objets.append(b"<< /Length " + str(len(contenu_bytes)).encode("ascii") + b" >>\nstream\n" + contenu_bytes + b"\nendstream")

    sortie = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    positions = []
    for i, obj in enumerate(objets, start=1):
        positions.append(len(sortie))
        sortie += f"{i} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
    debut_xref = len(sortie)
    sortie += f"xref\n0 {len(objets) + 1}\n".encode("ascii")
    sortie += b"0000000000 65535 f \n"
    for pos in positions:
        sortie += f"{pos:010d} 00000 n \n".encode("ascii")
    sortie += f"trailer\n<< /Size {len(objets) + 1} /Root 1 0 R >>\nstartxref\n{debut_xref}\n%%EOF".encode("ascii")
    return bytes(sortie)


def detecter_delimiteur(texte):
    """Devine le séparateur d'un fichier CSV (virgule ou point-virgule,
    fréquent dans les exports Excel en français)."""
    premiere_ligne = texte.splitlines()[0] if texte.splitlines() else ""
    return ";" if premiere_ligne.count(";") > premiere_ligne.count(",") else ","


def lire_csv_simple(contenu_bytes):
    texte = contenu_bytes.decode("utf-8-sig", errors="replace")
    return list(csv.reader(io.StringIO(texte), delimiter=detecter_delimiteur(texte)))


def lire_xlsx_simple(contenu_bytes):
    """Lit la première feuille d'un classeur .xlsx à l'aide, uniquement, des
    modules zipfile et xml.etree.ElementTree de la bibliothèque standard
    (pas de openpyxl ni d'autre bibliothèque externe). Limite connue :
    suppose des colonnes contiguës à partir de A, sans cellule vide isolée
    au milieu d'une ligne."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(contenu_bytes)) as z:
        chaines = []
        if "xl/sharedStrings.xml" in z.namelist():
            arbre = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in arbre.findall(f"{ns}si"):
                chaines.append("".join(t.text or "" for t in si.iter(f"{ns}t")))
        feuilles = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        if not feuilles:
            return []
        arbre = ET.fromstring(z.read(feuilles[0]))
        lignes = []
        for row in arbre.iter(f"{ns}row"):
            valeurs = []
            for cell in row.findall(f"{ns}c"):
                type_cellule = cell.get("t")
                if type_cellule == "inlineStr":
                    texte_cell = "".join(t.text or "" for t in cell.iter(f"{ns}t"))
                else:
                    v = cell.find(f"{ns}v")
                    texte_cell = v.text if v is not None else ""
                    if type_cellule == "s" and texte_cell != "":
                        try:
                            texte_cell = chaines[int(texte_cell)]
                        except (ValueError, IndexError):
                            texte_cell = ""
                valeurs.append(texte_cell)
            lignes.append(valeurs)
        return lignes


def construire_guide_pdf():
    marge_haut, marge_bas, x = 792, 56, 56
    pages, flux, y = [], [], [marge_haut]

    def nouvelle_page():
        pages.append("\n".join(flux))
        flux.clear()
        y[0] = marge_haut

    def ecrire(texte, taille=11, gras=False, avant=0, apres=15):
        if y[0] - avant - apres < marge_bas:
            nouvelle_page()
        y[0] -= avant
        police = "F2" if gras else "F1"
        flux.append(f"BT /{police} {taille} Tf {x} {y[0]:.0f} Td ({_pdf_echapper(texte)}) Tj ET")
        y[0] -= apres

    ecrire("Guide utilisateur", taille=20, gras=True, apres=8)
    ecrire("Portail HTIB ATLANTIS — inscription en ligne", taille=12, apres=30)
    for titre_section, paragraphes in SECTIONS_GUIDE:
        ecrire(titre_section, taille=13, gras=True, avant=8, apres=18)
        for p in paragraphes:
            for ligne in textwrap.wrap(p, width=92):
                ecrire(ligne, taille=11, apres=15)
            y[0] -= 6
    if flux or not pages:
        pages.append("\n".join(flux))
    return _pdf_assembler(pages)


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
        ("/personnel/dates", "Dates importantes", "dates"),
        ("/personnel/validation", "Validation Candidatures", "validation"),
        ("/personnel/examens", "Examens et notes", "examens"),
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

    def lire_multipart(self):
        """Lit un formulaire multipart/form-data (envoi de fichier compris),
        à la main et sans bibliothèque externe (ni le module cgi, obsolète).
        Retourne un dict nom_champ -> (contenu_bytes, nom_fichier_ou_None)."""
        resultat = {}
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return resultat
        boundary = None
        for morceau in ctype.split(";"):
            morceau = morceau.strip()
            if morceau.startswith("boundary="):
                boundary = morceau[len("boundary="):].strip('"')
        if not boundary:
            return resultat
        longueur = int(self.headers.get("Content-Length", 0) or 0)
        corps = self.rfile.read(longueur) if longueur else b""
        delimiteur = b"--" + boundary.encode("utf-8")
        for partie in corps.split(delimiteur):
            partie = partie.strip(b"\r\n")
            if not partie or partie == b"--":
                continue
            if b"\r\n\r\n" not in partie:
                continue
            entetes_brut, contenu = partie.split(b"\r\n\r\n", 1)
            if contenu.endswith(b"\r\n"):
                contenu = contenu[:-2]
            entetes = entetes_brut.decode("utf-8", errors="replace")
            m_nom = re.search(r'name="([^"]*)"', entetes)
            m_fichier = re.search(r'filename="([^"]*)"', entetes)
            if m_nom:
                resultat[m_nom.group(1)] = (contenu, m_fichier.group(1) if m_fichier else None)
        return resultat

    def champ_multipart(self, donnees, nom, defaut=""):
        if nom not in donnees:
            return defaut
        contenu, _ = donnees[nom]
        return contenu.decode("utf-8", errors="replace").strip()

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

    def servir_guide_pdf(self):
        data = construire_guide_pdf()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", 'attachment; filename="guide-utilisateur-portail-htib.pdf"')
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
        if chemin == "/guide.pdf":
            return self.servir_guide_pdf()
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
            "/personnel/dates": self.vue_staff_dates,
            "/personnel/examens": self.vue_staff_examens,
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
            "/personnel/dates": self.post_staff_dates,
            "/personnel/dates/supprimer": self.post_staff_dates_supprimer,
            "/personnel/examens/saisie": self.post_staff_examens_saisie,
            "/personnel/examens/import": self.post_staff_examens_import,
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
            dates = conn.execute("SELECT * FROM dates_importantes ORDER BY ordre, id").fetchall()
            lignes = "".join(f"<tr><td>{esc(d['cycle'])}</td><td>{esc(d['debut'] or '—')}</td><td>{esc(d['fin'] or '—')}</td></tr>" for d in dates)
            contenu_gauche = f"<h2>Dates importantes</h2><div class=\"card\"><h3>Inscriptions en ligne — Année 2026-2027</h3>{table(['Cycle', 'Début', 'Fin'], lignes, 'Aucune date publiée pour le moment.')}</div>"
        conn.close()

        side = f"""<aside class="side">
          <a href="/portail?vue=accueil" class="{'active' if vue == 'accueil' else ''}">Accueil</a>
          <a href="/portail?vue=offres" class="{'active' if vue == 'offres' else ''}">Offres de formation</a>
          <a href="/portail?vue=guide" class="{'active' if vue == 'guide' else ''}">Guide utilisateur</a>
          <a href="/personnel/connexion" class="deconnexion" style="color:#063b78;border-top:1px solid #e5eaf0;margin-top:8px;padding-top:12px">Espace personnel</a>
        </aside>"""
        if vue == "guide":
            contenu_gauche = (
                "<h2>Guide utilisateur</h2><div class=\"card\">"
                "<p>Créez un compte selon votre profil, déposez vos vœux de parcours, puis suivez "
                "les étapes (unités d'enseignement, paiement, fiche d'inscription) une fois votre "
                "parcours accordé par le service de la scolarité.</p>"
                "<a class=\"btn\" style=\"display:inline-block;text-decoration:none;margin-top:10px\" "
                "href=\"/guide.pdf\">Télécharger le guide complet (PDF)</a></div>"
            )

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
                notes_ue = conn.execute(
                    "SELECT session, note FROM notes WHERE candidat_id = ? AND ue_id = ? ORDER BY session", (s["id"], u["id"])
                ).fetchall()
                if notes_ue:
                    cellule_note = ", ".join(f"{esc(n['session'])} : {n['note']:.2f}/20" for n in notes_ue if n["note"] is not None)
                    cellule_note = cellule_note or '<span class="muted">Non disponible</span>'
                else:
                    cellule_note = '<span class="muted">Non disponible</span>'
                lignes += f"<tr><td>{esc(u['code'])}</td><td>{esc(u['libelle'])}</td><td>{cellule_note}</td></tr>"
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
        insc = inscription_de_candidat(conn, s["id"])
        lignes = ""
        notes_valides = []
        if insc:
            ues = conn.execute(
                """SELECT u.* FROM ue_catalogue u JOIN inscriptions_ue_items i ON u.id = i.ue_id
                   WHERE i.inscription_id = ? ORDER BY u.code""",
                (insc["id"],),
            ).fetchall()
            for u in ues:
                n = conn.execute(
                    "SELECT note FROM notes WHERE candidat_id = ? AND ue_id = ? ORDER BY (session = 'Normale') DESC, id DESC LIMIT 1",
                    (s["id"], u["id"]),
                ).fetchone()
                valeur = n["note"] if n and n["note"] is not None else None
                if valeur is not None:
                    notes_valides.append((valeur, u["credit"]))
                    cellule_note = f"{valeur:.2f}/20"
                else:
                    cellule_note = '<span class="muted">—</span>'
                lignes += f"<tr><td>{esc(u['code'])}</td><td>{esc(u['libelle'])}</td><td>{u['credit']}</td><td>{cellule_note}</td></tr>"
        conn.close()
        if notes_valides:
            total_credits = sum(c for _, c in notes_valides)
            moyenne = sum(v * c for v, c in notes_valides) / total_credits if total_credits else 0
            bas = f"<p><strong>Moyenne générale pondérée : {moyenne:.2f}/20</strong></p>"
        else:
            bas = '<p class="muted">Aucune note publiée pour le moment.</p>'
        contenu = f"""<h2>Relevé de notes</h2><div class="card">{table(['Code UE', 'Libellé', 'Crédit', 'Note'], lignes, "Aucune UE inscrite pour le moment.")}{bas}</div>
        <p class="muted">Relevé indicatif généré automatiquement à partir des notes publiées ; le relevé officiel est délivré par le service de la scolarité.</p>"""
        corps = f'<div class="espace-wrap"><aside class="side">{self.menu_espace("releves", candidat["profil"])}</aside><div>{contenu}</div></div>'
        self.envoyer_html(page("Relevé de notes", corps, nav_candidat()))

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

    def vue_staff_dates(self, qs=None):
        s = self.exiger("staff")
        if not s:
            return
        conn = db.connexion()
        dates = conn.execute("SELECT * FROM dates_importantes ORDER BY ordre, id").fetchall()
        conn.close()
        cartes = ""
        for d in dates:
            cartes += f"""<div class="card">
              <form class="form" method="post" action="/personnel/dates">
                <input type="hidden" name="id" value="{d['id']}">
                <input name="cycle" value="{esc(d['cycle'])}" placeholder="Cycle" required>
                <input name="debut" value="{esc(d['debut'] or '')}" placeholder="Début (jj/mm/aaaa)">
                <input name="fin" value="{esc(d['fin'] or '')}" placeholder="Fin (jj/mm/aaaa)">
                <button class="btn" type="submit">Enregistrer</button>
              </form>
              <form method="post" action="/personnel/dates/supprimer" style="margin-top:6px">
                <input type="hidden" name="id" value="{d['id']}">
                <button class="btn danger" type="submit">Supprimer cette ligne</button>
              </form>
            </div>"""
        contenu = f"""<h2>Dates importantes</h2>
        <p class="muted">Ces dates s'affichent sur la page d'accueil du portail (onglet « Dates importantes »). Modifiez une ligne existante ou ajoutez-en une nouvelle.</p>
        {cartes if cartes else '<div class="card muted">Aucune date enregistrée pour le moment.</div>'}
        <div class="card"><h3>Ajouter une ligne</h3>
          <form class="form" method="post" action="/personnel/dates">
            <input name="cycle" placeholder="Cycle (ex: Licence Professionnelle)" required>
            <input name="debut" placeholder="Début (jj/mm/aaaa)">
            <input name="fin" placeholder="Fin (jj/mm/aaaa)">
            <button class="btn" type="submit">Ajouter</button>
          </form>
        </div>"""
        self.envoyer_html(page("Dates importantes", contenu, nav_staff("dates")))

    def post_staff_dates(self):
        s = self.exiger("staff")
        if not s:
            return
        donnees = self.lire_formulaire()
        cycle = self.champ(donnees, "cycle").strip()
        debut = self.champ(donnees, "debut").strip()
        fin = self.champ(donnees, "fin").strip()
        id_brut = self.champ(donnees, "id").strip()
        if cycle:
            conn = db.connexion()
            if id_brut:
                conn.execute("UPDATE dates_importantes SET cycle = ?, debut = ?, fin = ? WHERE id = ?", (cycle, debut, fin, int(id_brut)))
            else:
                ordre_max = conn.execute("SELECT COALESCE(MAX(ordre), 0) AS m FROM dates_importantes").fetchone()["m"]
                conn.execute("INSERT INTO dates_importantes (cycle, debut, fin, ordre) VALUES (?, ?, ?, ?)", (cycle, debut, fin, ordre_max + 1))
            conn.commit()
            conn.close()
        self.rediriger("/personnel/dates")

    def post_staff_dates_supprimer(self):
        s = self.exiger("staff")
        if not s:
            return
        donnees = self.lire_formulaire()
        id_brut = self.champ(donnees, "id", "0")
        conn = db.connexion()
        conn.execute("DELETE FROM dates_importantes WHERE id = ?", (int(id_brut or 0),))
        conn.commit()
        conn.close()
        self.rediriger("/personnel/dates")

    # ------------------------------------------------------------------
    # Examens et notes (saisie directe ou import CSV/Excel)
    # ------------------------------------------------------------------
    def vue_staff_examens(self, qs=None):
        s = self.exiger("staff")
        if not s:
            return
        qs = qs or {}
        program_id = (qs.get("programId") or [""])[0]
        ue_id = (qs.get("ueId") or [""])[0]
        session_examen = (qs.get("session") or ["Normale"])[0]
        message = (qs.get("message") or [""])[0]

        conn = db.connexion()
        progs = conn.execute("SELECT * FROM programs ORDER BY name").fetchall()
        options_progs = "".join(
            f'<option value="{p["id"]}" {"selected" if str(p["id"]) == program_id else ""}>{esc(p["name"])}</option>'
            for p in progs
        )
        ues_du_parcours = []
        if program_id:
            ues_du_parcours = conn.execute(
                "SELECT * FROM ue_catalogue WHERE program_id = ? ORDER BY code", (program_id,)
            ).fetchall()
        options_ues = "".join(
            f'<option value="{u["id"]}" {"selected" if str(u["id"]) == ue_id else ""}>{esc(u["code"])} — {esc(u["libelle"])}</option>'
            for u in ues_du_parcours
        )
        toutes_ues = conn.execute(
            "SELECT u.*, p.name AS programme_nom FROM ue_catalogue u JOIN programs p ON u.program_id = p.id ORDER BY p.name, u.code"
        ).fetchall()
        options_toutes_ues = "".join(
            f'<option value="{u["id"]}">{esc(u["programme_nom"])} — {esc(u["code"])} : {esc(u["libelle"])}</option>'
            for u in toutes_ues
        )

        bloc_saisie = ""
        if ue_id:
            etudiants = conn.execute(
                """SELECT c.id, c.nom, c.prenom, c.matricule FROM candidats c
                   JOIN inscriptions_ue iu ON iu.candidat_id = c.id
                   JOIN inscriptions_ue_items iui ON iui.inscription_id = iu.id
                   WHERE iui.ue_id = ? ORDER BY c.nom, c.prenom""",
                (ue_id,),
            ).fetchall()
            if etudiants:
                lignes_saisie = ""
                for e in etudiants:
                    existante = conn.execute(
                        "SELECT note FROM notes WHERE candidat_id = ? AND ue_id = ? AND session = ?",
                        (e["id"], ue_id, session_examen),
                    ).fetchone()
                    valeur = "" if not existante or existante["note"] is None else existante["note"]
                    lignes_saisie += (
                        f"<tr><td>{esc(e['nom'])} {esc(e['prenom'])}</td><td>{esc(e['matricule'] or '—')}</td>"
                        f'<td><input name="note_{e["id"]}" type="number" step="0.01" min="0" max="20" value="{valeur}" style="width:90px"></td></tr>'
                    )
                bloc_saisie = f"""<div class="card"><h3>Saisie directe des notes — session {esc(session_examen)}</h3>
                <form method="post" action="/personnel/examens/saisie">
                  <input type="hidden" name="ueId" value="{ue_id}">
                  <input type="hidden" name="session" value="{esc(session_examen)}">
                  {table(['Étudiant', 'Matricule', 'Note /20'], lignes_saisie)}
                  <button class="btn" type="submit" style="margin-top:10px">Enregistrer les notes</button>
                </form></div>"""
            else:
                bloc_saisie = '<div class="card muted">Aucun étudiant inscrit à cette UE pour le moment.</div>'
        conn.close()

        selecteur = f"""<div class="card"><h3>Choisir une UE</h3><form class="form" method="get" action="/personnel/examens">
          <select name="programId"><option value="">Choisir un parcours…</option>{options_progs}</select>
          <select name="ueId"><option value="">Choisir une UE…</option>{options_ues}</select>
          <input name="session" value="{esc(session_examen)}" placeholder="Session (ex: Normale, Rattrapage)">
          <button class="btn" type="submit">Afficher les étudiants</button>
        </form><p class="muted">Choisissez d'abord le parcours et validez, la liste des UE de ce parcours apparaît ensuite.</p></div>"""

        bloc_import = f"""<div class="card"><h3>Importer les notes depuis un fichier (CSV ou Excel .xlsx)</h3>
        <p class="muted">Le fichier doit contenir une ligne d'en-têtes avec au moins une colonne « identifiant » ou « matricule », et une colonne « note ». Seule la première feuille d'un fichier Excel est lue.</p>
        <form method="post" action="/personnel/examens/import" enctype="multipart/form-data">
          <select name="ueId" required><option value="">UE concernée…</option>{options_toutes_ues}</select>
          <input name="session" value="Normale" placeholder="Session (ex: Normale, Rattrapage)">
          <input type="file" name="fichier" accept=".csv,.xlsx" required>
          <button class="btn" type="submit">Importer</button>
        </form></div>"""

        bandeau = f'<div class="card" style="border-color:#063b78"><p>{esc(message)}</p></div>' if message else ""
        contenu = f"<h2>Examens et notes</h2>{bandeau}{selecteur}{bloc_saisie}{bloc_import}"
        self.envoyer_html(page("Examens et notes", contenu, nav_staff("examens")))

    def post_staff_examens_saisie(self):
        s = self.exiger("staff")
        if not s:
            return
        donnees = self.lire_formulaire()
        ue_id = int(self.champ(donnees, "ueId", "0") or 0)
        session_examen = self.champ(donnees, "session", "Normale").strip() or "Normale"
        program_id = ""
        if ue_id:
            conn = db.connexion()
            ue = conn.execute("SELECT program_id FROM ue_catalogue WHERE id = ?", (ue_id,)).fetchone()
            program_id = ue["program_id"] if ue else ""
            for cle in donnees:
                if cle.startswith("note_"):
                    valeur = self.champ(donnees, cle, "").strip()
                    if valeur == "":
                        continue
                    try:
                        note = max(0.0, min(20.0, float(valeur)))
                    except ValueError:
                        continue
                    candidat_id = int(cle[len("note_"):])
                    conn.execute(
                        "INSERT INTO notes (candidat_id, ue_id, note, session) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(candidat_id, ue_id, session) DO UPDATE SET note = excluded.note",
                        (candidat_id, ue_id, note, session_examen),
                    )
            conn.commit()
            conn.close()
        self.rediriger(f"/personnel/examens?programId={program_id}&ueId={ue_id}&session={quote(session_examen)}")

    def post_staff_examens_import(self):
        s = self.exiger("staff")
        if not s:
            return
        champs = self.lire_multipart()
        ue_id = int(self.champ_multipart(champs, "ueId", "0") or 0)
        session_examen = self.champ_multipart(champs, "session", "Normale").strip() or "Normale"
        fichier = champs.get("fichier")
        message = "Choisissez une UE et un fichier."

        if ue_id and fichier:
            contenu, nom_fichier = fichier
            try:
                if nom_fichier and nom_fichier.lower().endswith(".xlsx"):
                    lignes = lire_xlsx_simple(contenu)
                else:
                    lignes = lire_csv_simple(contenu)
            except Exception:
                lignes = []

            if not lignes:
                message = "Le fichier n'a pas pu être lu. Vérifiez qu'il s'agit bien d'un CSV ou d'un .xlsx valide."
            else:
                entetes = [str(c).strip().lower() for c in lignes[0]]
                idx_ident = entetes.index("identifiant") if "identifiant" in entetes else None
                idx_matricule = entetes.index("matricule") if "matricule" in entetes else None
                idx_note = entetes.index("note") if "note" in entetes else None
                if idx_note is None or (idx_ident is None and idx_matricule is None):
                    message = "Colonnes attendues introuvables : il faut une colonne « identifiant » ou « matricule », et une colonne « note »."
                else:
                    conn = db.connexion()
                    nb_importees = 0
                    for ligne in lignes[1:]:
                        if len(ligne) <= idx_note:
                            continue
                        valeur_brute = str(ligne[idx_note]).strip().replace(",", ".")
                        if valeur_brute == "":
                            continue
                        try:
                            note = max(0.0, min(20.0, float(valeur_brute)))
                        except ValueError:
                            continue
                        candidat = None
                        if idx_ident is not None and len(ligne) > idx_ident and str(ligne[idx_ident]).strip():
                            candidat = conn.execute(
                                "SELECT id FROM candidats WHERE identifiant = ?", (str(ligne[idx_ident]).strip(),)
                            ).fetchone()
                        if not candidat and idx_matricule is not None and len(ligne) > idx_matricule and str(ligne[idx_matricule]).strip():
                            candidat = conn.execute(
                                "SELECT id FROM candidats WHERE matricule = ?", (str(ligne[idx_matricule]).strip(),)
                            ).fetchone()
                        if not candidat:
                            continue
                        conn.execute(
                            "INSERT INTO notes (candidat_id, ue_id, note, session) VALUES (?, ?, ?, ?) "
                            "ON CONFLICT(candidat_id, ue_id, session) DO UPDATE SET note = excluded.note",
                            (candidat["id"], ue_id, note, session_examen),
                        )
                        nb_importees += 1
                    conn.commit()
                    conn.close()
                    message = f"{nb_importees} note(s) importée(s) avec succès." if nb_importees else "Aucune ligne valide n'a été trouvée (identifiant/matricule inconnu ou note manquante)."
        self.rediriger(f"/personnel/examens?ueId={ue_id}&session={quote(session_examen)}&message={quote(message)}")

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
