# Design — social2shorts (motoride-mh)

> Repurposer les **meilleures** vidéos moto de @motoride.mh (TikTok / Instagram)
> en **YouTube Shorts qualité pro, propres et monétisables** sur @motoride-mh.
> Statut : **design finalisé, validé par spike sur données réelles (2026-10-19)**.
> Dossier projet : `C:\workdir\uw\scraper\insta2yorube`.

---

## 1. Intention & pourquoi
L'utilisateur a un gros catalogue moto déjà produit (≥ 222 posts TikTok, mêmes
vidéos sur Instagram) mais **rien sur YouTube**. But : **faire grandir la chaîne
YouTube @motoride-mh** en y republiant, en **Shorts**, la *crème* du contenu,
proprement, en qualité pro, sans **griller la monétisation**. La valeur tient à
**la curation + la transformation**, pas au volume.

## 2. Décisions de l'utilisateur (actées)
- **Portée** : backlog **+** mode continu (nouvelles vidéos en auto ensuite).
- **Sélection** : **une sélection des meilleures** (exclure micro-clips < ~12 s
  et diaporamas photo).
- **Source média** : **les originaux 1080p+ du téléphone** (app **Edits**), pas
  le scraping. Couverture : **presque toutes** les vidéos.
- **Transformation** : **légère et auto** (sous-titres + titre/description
  refaits). On enrichit seulement si YouTube le réclame.
- **Garde-fou audio** : son original → publié auto ; musique sous licence →
  file d'attente.

## 3. Faits confirmés par le spike (2026-10-19)
- **TikTok** @motoride.mh : **≥ 222 vidéos**, téléchargeables **sans login** et
  **sans watermark** via `yt-dlp` — mais plafonnées à **~540p** (la version HD
  n'existe qu'avec watermark). Métadonnées riches dispo : légende, vues, likes,
  date, et **`track`/`artist`** (→ détection audio fiable ; ex. vu :
  `track="original sound"`).
- **Originaux téléphone** (S23 Ultra en USB/**MTP**, lisible via COM
  `Shell.Application`) :
  - `Movies/Edits` : **33 reels finis** (`VID_..._bsl.mp4`).
  - `Movies/Instagram` : 23 vidéos. `Movies/Insta_⚡` : 1. (Rushes bruts dans les
    sous-dossiers `DCIM/…`.)
  - Qualité mesurée : **1440×2560 (QHD vertical), HEVC/H.265, 30 fps, ~12 Mbps,
    AAC stéréo**. ⇒ **au-dessus du 1080p**, propre, sans watermark.
- **YouTube** : `videos.insert` + OAuth, **100 uploads/j** (large), **Short auto**
  (vertical ≤ 3 min). **Automatisation viable** : app OAuth en *Production
  (non vérifiée)* → **refresh token non expirant** ; restriction « upload privé
  si non audité » a priori **levée depuis oct. 2026** (à reconfirmer au build).

## 4. Objectifs / Non-objectifs / Anti-objectifs
**Objectifs** : curation des meilleures ; média final **1080×1920 propre** issu
des originaux ; sécurité monétisation (garde-fou audio + valeur ajoutée) ;
anti-doublon IG/TikTok ; backlog + continu.
**Non-objectifs** : tout republier ; suppression watermark IA en voie
principale ; IG comme source *média* en Phase 1 (login requis) ; toucher au
contenu d'autrui.
**Anti-objectifs (= échec)** : chaîne flaguée « contenu réutilisé / inauthentique »
et démonétisée ; doublons ; vidéos basse qualité/watermarkées ; claim/strike
Content ID.

## 5. Monétisation — lecture honnête (risque assumé)
- C'est **ton** contenu filmé → la politique **« contenu réutilisé »** (qui vise
  surtout le contenu **des autres**) te concerne peu.
- Le vrai risque est la politique **« contenu inauthentique »** (ex-« répétitif »,
  renommée 07/2025) : **répétitif / en masse / faible variation** → sanction
  possible **au niveau chaîne**. La curation (vidéos variées, pas de diaporama) +
  les sous-titres + titres refaits aident, mais **le « léger auto » n'est pas
  garanti suffisant**. ⇒ On **démarre en « non répertorié » sur 3-5 vidéos**,
  on observe, et le **plan B documenté** est d'enrichir (voix off/commentaire,
  intro/outro, fusion de séries) si YouTube tique.
- **La monétisation n'est jamais garantie** (décision YouTube ; les seuils
  abonnés/vues sont un sujet séparé). On **maximise les chances**.

## 6. Architecture
```
[1] Découverte   yt-dlp (profil TikTok) → catalogue SQLite
                 (id, légende, durée, vues, likes, date, track/artist)
[2] Curation     filtres (durée ≥ 12 s, pas de diaporama) + score engagement
                 → shortlist ordonnée ; whitelist/blacklist possible
[3] Média        ingestion originaux (MTP→disque) ; match original ↔ post
                 par DURÉE + empreinte visuelle (pHash), insensible à la résolution
[4] Audio        track == original/vide → auto ; sinon → review_queue
[5] Enrichissement  Whisper → sous-titres incrustés (si parole) ;
                 LLM → titre + description + hashtags YouTube (reformulés) ;
                 transcode → 1080×1920 H.264 (depuis le QHD HEVC)
[6] Upload       YouTube Data API v3 (OAuth refresh token) ;
                 1re tournée en "unlisted" → revue → public/planifié
[7] Ledger       empreinte → statut/youtube_id (anti-doublon IG+TikTok, idempotent)
```

## 7. Décisions builder (post-revue, mes choix)
- **Matching** : clé primaire = **durée** (tolérance ±1 s) croisée avec
  **pHash** de N frames échantillonnées (le QHD vertical et le 540p TikTok
  partagent le cadrage → pHash robuste). **Match ambigu** (plusieurs candidats,
  séries auto-similaires) → **file de revue**, jamais de choix auto risqué
  (anti-objectif). Seuil de confiance configurable.
- **Ingestion originaux** : copie **MTP → `originals/`** via COM `CopyHere`
  (validé). État `pending_original` dans la base si un post n'a pas encore son
  original ; en continu, on **ne publie pas** un fallback 540p sans accord.
- **Revue** : CLI (`review`) pour valider la file audio et **basculer
  unlisted→public via l'API** après OK.
- **Scheduler** : relance périodique (Planificateur Windows) ; sur échec de
  refresh token → **alerte + pause**, jamais d'arrêt silencieux ; token chiffré
  hors dépôt.
- **Garde-fou audio renforcé** : tag `track` **+** contrôle **Content ID
  post-upload** (une musique d'ambiance peut passer le pré-filtre) → dépublie/alerte.
- **Séries multi-parties** : en Phase 1, **chaque partie = un Short séparé** (pas
  de fusion ; la fusion = option Phase 2 si enrichissement).
- **Sous-titres** : incrustés **quand il y a de la parole** (sinon on saute).
- **Transcode** : QHD/HEVC → **1080×1920 H.264** (compat maximale Shorts) ;
  on garde l'option de pousser plus haut si besoin.

## 8. Stack
Python 3.12 · `yt-dlp` (2026.08.19, présent) · `ffmpeg`/`ffprobe` (présents) ·
`faster-whisper` · `imagehash` (pHash) · `google-api-python-client` +
`google-auth-oauthlib` · SQLite (stdlib) · accès MTP via `Shell.Application`
(pywin32/COM) · LLM gratuit pour les titres (contenu **public**, aucune donnée
sensible dans ce flux).

## 9. Phasage
- **Phase 0 — Spike : FAIT** (ci-dessus, tout validé).
- **Phase 1 — MVP** : découverte + catalogue + curation ; ingestion + matching
  sur les **Edits** ; garde-fou audio ; sous-titres + titres IA ; **upload de
  3-5 vidéos en `unlisted`** pour validation du rendu avant de scaler.
- **Phase 2 — Échelle & continu** : sélection élargie ; scheduler (nouvelles
  vidéos) ; IG en découverte secondaire ; enrichissement (voix off, fusion de
  séries) si la monétisation l'exige ; contrôle Content ID systématique.

## 10. Dépendances / à la charge de l'utilisateur
- **Compte développeur Google** + création du projet OAuth (je guide pas à pas)
  et acceptation de l'écran « app non vérifiée » (compte perso).
- **Téléphone branché** (MTP) au moment des ingestions backlog ; pour le continu,
  idéalement les nouveaux reels exportés via Edits avant le passage planifié.
- Validation humaine des 3-5 Shorts de test (non répertoriés) avant passage public.
