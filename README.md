# Portail HTIB ATLANTIS (version 100% Python, sans JavaScript)

Vraie application web : un serveur, une vraie base de données partagée
(SQLite), et deux espaces séparés — candidats/étudiants et personnel — qui
peuvent se connecter en même temps, depuis des appareils différents.

**Écrite entièrement en Python**, comme ASSOGEST et CliniGest. Chaque clic
dans le navigateur est un simple lien ou un formulaire HTML classique : le
navigateur envoie la demande au serveur, qui renvoie une page HTML complète
en retour — comme un site web "à l'ancienne". **Aucun JavaScript** ne tourne
dans le navigateur.

## 1. Ce que ça couvre (v2 — fidèle au guide PAUL de l'Université de Lomé)

- Comptes personnel et candidats/étudiants, avec mot de passe (jamais stocké
  en clair : haché avec `scrypt`).
- Portail public (inspiré de PAUL) : dates importantes, offres de formation,
  connexion, création de compte selon **5 profils** : Nouveau bachelier,
  Ancien bachelier, Étudiant (déjà inscrit), BAC étranger, Master
  Professionnel.
- **Circuit bachelier (Licence/BTS)** : identification + mot de passe →
  jusqu'à 3 vœux de parcours par ordre de préférence → validation par le
  personnel → unités d'enseignement (5 onglets, voir ci-dessous) → paiement
  → fiche d'inscription → dépôt définitif.
- **Réinscription et réorientation** (profil Étudiant) : un étudiant déjà
  inscrit peut confirmer sa réinscription dans son parcours actuel, ou
  déposer une demande de réorientation vers un autre parcours — chaque
  demande est tracée par type (Nouvelle inscription / Réinscription /
  Réorientation / Master Professionnel) et validée par le personnel comme
  les vœux classiques.
- **Master Professionnel** : circuit simplifié adapté du chapitre
  Master/Doctorat du guide PAUL — une demande d'inscription directe à un
  parcours de Master Pro (sans classement de vœux), puis le même circuit
  paiement/fiche/dépôt, avec une pièce « diplôme de Licence ou BTS » en plus
  dans le dossier à déposer.
- **Bacheliers étrangers** : dossier de dépôt enrichi des pièces
  d'équivalence (attestation d'équivalence, légalisation consulaire du
  diplôme).
- **Unités d'enseignement en 5 onglets**, comme sur PAUL : UE obligatoires
  (auto-inscrites), UE libres (choix), Désinscription (retirer une UE
  libre), UE choisies (récapitulatif), UE confirmées (après paiement).
- **Résultats** : Notes (par UE inscrite, par session), Cursus (historique
  des demandes/parcours), Relevés de notes (moyenne générale pondérée par
  les crédits, calculée automatiquement).
- **Œuvres universitaires** : demande de logement, fiche d'analyse médicale
  (paiement des frais), prise de rendez-vous — chacune suivie et traitée
  côté personnel.
- **Dates importantes** modifiables par le personnel (ajout/modification/
  suppression), affichées en direct sur la page d'accueil du portail.
- **Guide utilisateur téléchargeable en PDF** depuis le portail public
  (fichier généré par l'application elle-même, sans bibliothèque externe).
- **Examens et notes** côté personnel : saisie directe des notes par UE et
  par session (Normale, Rattrapage...) pour tous les étudiants inscrits à
  cette UE, ou import en masse depuis un fichier **CSV ou Excel (.xlsx)** —
  colonnes attendues : « identifiant » ou « matricule », et « note ». La
  lecture du fichier Excel est faite par l'application elle-même (modules
  `zipfile` et `xml` de la bibliothèque standard), sans openpyxl ni aucune
  autre dépendance à installer.
- Côté personnel : tableau de bord détaillé (candidatures par type, vœux en
  attente, demandes de logement/rendez-vous en attente), validation des
  candidatures (avec le type de chaque demande), carte d'étudiant, module
  Étudiants (fiche simple), suivi des paiements, gestion des demandes
  d'œuvres universitaires.
- **Comptes candidats** (page Validation) : le personnel peut retrouver
  l'identifiant de n'importe quel candidat et réinitialiser son mot de passe
  en un clic (un nouveau mot de passe est généré et affiché une seule fois,
  à transmettre au candidat) — utile quand un candidat a oublié son
  identifiant ou son mot de passe, qui ne sont affichés qu'une seule fois à
  l'inscription.
- **Sessions de connexion persistantes** : les connexions (personnel et
  candidats) sont désormais enregistrées dans la base de données plutôt
  qu'en mémoire — un redémarrage du serveur (mise en veille sur le plan
  gratuit Render, redéploiement) ne déconnecte plus les utilisateurs.

Pas encore dans cette version (à ajouter ensuite) : présences, emploi du
temps, communication, rapports imprimables, gestion des comptes du
personnel, années académiques, paramètres.

## 2. Lancer l'application sur votre ordinateur

Prérequis : **Python 3.9 ou plus récent** (déjà installé sur la plupart des
PC ; sinon téléchargez-le sur https://python.org). Aucune bibliothèque à
installer avec `pip` — tout vient de la bibliothèque standard de Python.

Vérifiez votre version :
```
python3 --version
```
(sous Windows, utilisez `py --version` si `python3` n'est pas reconnu).

Puis, dans le dossier du projet :
```
python3 app.py
```
(sous Windows : `py app.py`)

Ouvrez ensuite http://localhost:3000 dans votre navigateur.

Un compte personnel est créé automatiquement au premier démarrage :
- Identifiant : `admin`
- Mot de passe : `htib2026`

**Changez ce mot de passe avant toute mise en production.**

## 3. Mettre en ligne gratuitement un lien de démonstration (Render.com)

Pour obtenir tout de suite un lien partageable (par WhatsApp, etc.) **sans
dépenser d'argent et sans carte bancaire**, ce dossier contient déjà les
fichiers nécessaires (`render.yaml`, `runtime.txt`) pour un déploiement en
quelques clics sur [Render.com](https://render.com), qui propose un plan
gratuit.

**Point important à connaître avant de commencer :** sur ce plan gratuit,
le stockage est *temporaire* — si le service reste inactif un moment, il se
met en veille, et au redémarrage, la base de données `htib.db` repart à
zéro (comptes et inscriptions effacés). C'est très bien pour montrer
l'application à des gens via un lien, mais **pas encore adapté pour de
vraies inscriptions d'étudiants** — pour ça, il faudra passer à l'étape 4
(VPS) le jour où le budget le permet.

Étapes (je ne peux pas créer les comptes à votre place, mais je vous guide
sur chacune) :

1. **Créez un compte GitHub gratuit** sur github.com (juste un e-mail, pas
   de carte bancaire), puis créez un nouveau dépôt (par exemple
   `portail-htib`) et déposez-y tous les fichiers de ce dossier via le
   bouton "Add file → Upload files" du site GitHub (pas besoin d'installer
   d'outil en ligne de commande).
2. **Créez un compte Render gratuit** sur render.com, avec le bouton
   "Sign up" (connexion possible directement avec le compte GitHub de
   l'étape 1, pas de carte bancaire demandée pour le plan gratuit).
3. Dans Render, cliquez sur **New + → Blueprint**, puis choisissez le dépôt
   GitHub `portail-htib` : Render lit automatiquement le fichier
   `render.yaml` du projet et propose de créer le service — cliquez sur
   **Apply**.
4. Au bout de quelques minutes, Render affiche un lien du type
   `https://portail-htib.onrender.com` — c'est le lien à partager. La
   première ouverture après une veille peut prendre environ une minute (le
   service redémarre), c'est normal sur le plan gratuit.

Dites-moi à quelle étape vous êtes si quelque chose coince, je vous aide à
avancer.

## 4. Déployer en ligne de façon durable, avec nom de domaine et HTTPS

Pour que les étudiants et le personnel y accèdent en permanence (comme
PAUL), avec une adresse du type `https://portail-htib.com` trouvable sur
Google et des données qui ne sont jamais effacées, il faut un VPS payant
(voir la limite du plan gratuit ci-dessus). **Je ne peux pas acheter le
domaine ni le VPS à votre place** (ce sont des paiements et des comptes qui
doivent être les vôtres), mais je vous accompagne commande par commande sur
chaque étape, comme pour l'installateur ASSOGEST, le jour où vous êtes
prêt.

### Étape 1 — Réserver un nom de domaine

Chez un registrar : Namecheap, OVH, ou un registrar togolais pour un `.tg`.
Quelques milliers de FCFA par an.

### Étape 2 — Louer un VPS (serveur privé virtuel)

Un petit VPS Ubuntu chez Hostinger, Contabo ou DigitalOcean suffit largement
(quelques milliers de FCFA/mois). Une fois le VPS créé, vous recevez une
adresse IP et un accès SSH — donnez-les-moi et je vous guide pour :

1. installer Python 3 sur le serveur (déjà présent sur la plupart des
   images Ubuntu) ;
2. copier ce dossier sur le serveur (`scp` ou `git`) ;
3. faire tourner `app.py` en continu avec `systemd`, pour qu'il redémarre
   automatiquement si le serveur reboote ;
4. installer **nginx** devant l'application comme reverse proxy (pour
   écouter sur le port 80/443 standard au lieu du port 3000) ;
5. activer le **HTTPS gratuit** avec `certbot` (Let's Encrypt), pour le
   cadenas vert dans le navigateur.

### Étape 3 — Pointer le domaine vers le serveur et activer le HTTPS

Dans le panneau de gestion DNS de votre registrar (étape 1), on ajoute un
enregistrement **A** qui pointe votre nom de domaine vers l'adresse IP du
VPS (étape 2). La propagation prend de quelques minutes à quelques heures.
`certbot` (étape 2) émet alors un certificat HTTPS valable pour ce domaine.

Une fois le domaine actif, démarrez l'application avec la variable
`DOMAINE_PUBLIC` (utilisée pour le sitemap et les balises SEO) :
```
DOMAINE_PUBLIC=https://portail-htib.com python3 app.py
```

### Étape 4 — Être trouvable sur Google

L'application expose déjà `/robots.txt` et `/sitemap.xml`, et chaque page a
une balise `<meta name="description">`. Une fois le site en ligne avec son
nom de domaine :

1. Créez un compte gratuit sur **Google Search Console**
   (search.google.com/search-console).
2. Ajoutez votre domaine, vérifiez la propriété (Google vous donne une
   méthode simple : enregistrement DNS ou fichier à déposer).
3. Soumettez `https://votre-domaine/sitemap.xml`.

Google indexe généralement le site en quelques jours à quelques semaines —
impossible d'être plus rapide, c'est Google qui décide de son rythme
d'exploration. Entre-temps, le site est déjà accessible normalement par son
adresse directe.

## 5. Structure du projet

```
portail-htib/
  app.py          serveur HTTP + toutes les pages (aucune dépendance externe)
  db.py           schéma de la base SQLite + données de départ
  htib.db         la base de données (créée au premier lancement)
  render.yaml     configuration de déploiement gratuit sur Render.com
  runtime.txt     version de Python à utiliser sur Render
  static/
    style.css     mise en page, couleurs HTIB ATLANTIS
    logo.png      logo officiel détouré
```

## 6. Mise à jour depuis une version précédente

Si vous avez déjà utilisé une version antérieure de cette application (avec
un fichier `htib.db` existant), copiez simplement ce `htib.db` dans le
nouveau dossier avant de lancer `app.py` : au démarrage, l'application met
à jour automatiquement la structure de la base (nouvelles colonnes, nouveaux
parcours Master Professionnel) **sans effacer aucune donnée déjà
enregistrée** (candidats, demandes, paiements...). Ceci ne s'applique pas
au déploiement Render gratuit de la section 3, dont le stockage est
temporaire par nature.

## 7. Limites connues

Aucune limite bloquante connue à ce jour pour une démonstration ou un usage
avec le plan gratuit Render. Pour rappel, sur ce plan gratuit, le stockage
(`htib.db`) reste temporaire (voir section 3) : les sessions, elles,
survivent désormais à un redémarrage du serveur, mais si le fichier
`htib.db` lui-même est effacé (veille prolongée sur Render), tout repart à
zéro — comptes, candidatures et sessions compris. Pour des données qui ne
sont jamais effacées, voir l'étape 4 (VPS).
