# CLAUDE.md

Briefing court pour Claude Code travaillant sur ce repo.

## Quoi

Pipeline Python qui interroge l'API **Peppol Directory** et produit un rapport
quotidien d'adoption des doctypes France (UBL/CII CIUS & EXTENDED, Factur-X, CDAR).

- `generate_peppol_report.py` — CLI unique. Deux modes : `brief` (défaut, run
  quotidien) et `--detailed` (analyse ponctuelle/mensuelle).
- `peppol_report_brief.html.j2` — template Jinja2 du rapport brief.
- `peppol_history.json` — mémoire des runs, **versionnée à la racine**, une
  entrée par jour (`YYYY-MM-DD`). Un run du jour écrase l'entrée du jour.
- `peppol_report_template.html.j2` — template du mode `--detailed`, versionné
  pour permettre l'exécution en local. Le pipeline automatique ne l'utilise
  pas (publication limitée au brief).

Le mode `--detailed` peut inclure (via `--enable-smp-lookup`) une section
**« Couverture par SMP »** qui résout chaque participant via le SML
(DNS public Peppol, stdlib `socket` ou DoH `requests`) pour agréger la
palette de doctypes par domaine racine de SMP. ~6000 lookups DNS en
thread pool, 1-2 min de run. **Désactivée par défaut** depuis mai 2026 :
OpenPeppol a migré le SML hors CEF eDelivery vers la zone in-house
`participant.sml.prod.tech.peppol.org` (deadline SMP 31/05/2026, AP
Lookup 31/08/2026), et pendant la transition le SML est vide pour les
participants français. À réactiver post-août 2026.

## Commandes utiles

```bash
pip install -r requirements.txt
sudo apt install libcairo2 libpango-1.0-0 libpangoft2-1.0-0  # WeasyPrint

# Run quotidien : historique à la racine, sortie dans ./out
python generate_peppol_report.py --history peppol_history.json --output-dir ./out

# Re-rendu sans appeler l'API (utile pour itérer sur le template)
python generate_peppol_report.py --no-api --history peppol_history.json --output-dir ./out

# HTML seulement (évite WeasyPrint)
python generate_peppol_report.py --no-pdf --history peppol_history.json --output-dir ./out
```

Codes de sortie : `0` OK · `2` échec collecte/historique · `3` HTML OK mais
PDF KO.

## Publication automatique

Workflow GitHub Actions `.github/workflows/daily-report.yml` :

- Cron quotidien `17 5 * * *` UTC ≈ **07:17 Europe/Paris** en heure d'été
  (CEST, UTC+2). En heure d'hiver (CET, UTC+1) le run tombe à 06:17 Paris —
  ajuster le cron deux fois par an si la précision horaire compte.
- Décalage volontaire de l'heure pile : GitHub documente que les schedules
  à `:00` sont retardés ou skippés pendant les pics de charge.
- Déclenchement manuel `workflow_dispatch` également disponible.
- Étapes : install deps → run script en mode brief `--no-pdf` (HTML
  uniquement, pas de WeasyPrint) → commit `peppol_history.json` mis à
  jour sur la branche par défaut → publie le HTML sur **GitHub Pages**
  (`sandjab.github.io/peppol`).
- Le workflow ne déclenche son cron que depuis la branche par défaut (`main`)
  — toute modification doit y être mergée pour être active.

Pré-requis côté repo GitHub :

1. Settings → Pages → **Source = GitHub Actions**.
2. Settings → Actions → General → Workflow permissions = **Read and write**
   (ou laisser au workflow son `permissions: contents: write`).

## Conventions

- Branche de dev courante : **`claude/charming-carson-qmaFX`** (cf. consigne
  session). Merger vers `main` pour activer le cron.
- Tout commit qui change l'historique du jour doit aussi régénérer le HTML
  publié (le workflow s'en charge automatiquement).
- Pas de secrets nécessaires : l'API Peppol Directory est publique.
