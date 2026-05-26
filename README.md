# Générateur du rapport PEPPOL France · adoption EXTENDED-CTC-FR

Pipeline reproductible qui interroge l'API Peppol Directory et produit deux rapports possibles :

- **Mode brief (défaut)** — comptages bruts du jour + table d'évolution (Δ J−1 / J−7 / origine) + 2 graphiques SVG d'évolution. Conçu pour un run quotidien automatisé.
- **Mode `--detailed`** — analyse complète avec KPIs, signatures de doctypes, échantillon d'entités. Conçu pour un rapport ponctuel ou mensuel.

## Fichiers du bundle

| Fichier | Rôle |
|---|---|
| `generate_peppol_report.py` | Script CLI |
| `peppol_report_brief.html.j2` | Template Jinja2 du rapport brief |
| `peppol_report_template.html.j2` | Template Jinja2 du rapport détaillé |
| `peppol_history.json` | Mémoire des runs (1 entrée par jour, JSON enrichi à chaque exécution) |
| `peppol_brief_sample.{html,pdf}` | Exemple de sortie brief (rendu de l'historique 24+26 mai) |

## Installation

```bash
pip install requests jinja2 weasyprint

WeasyPrint nécessite des libs système (Debian/Ubuntu) :
```bash
sudo apt install libcairo2 libpango-1.0-0 libpangoft2-1.0-0
```

## Usage quotidien

```bash
python generate_peppol_report.py
```

- Interroge l'API pour 6 doctypes France (~10 secondes)
- Ajoute / écrase l'entrée du jour dans `./out/peppol_history.json`
- Génère `./out/peppol_extension_report.{html,pdf}` à partir de tout l'historique

Idéal en cron daily.

## Options CLI

```
--output-dir, -o      Répertoire de sortie (défaut : ./out)
--template-brief      Chemin du template brief (défaut : ./peppol_report_brief.html.j2)
--template-detailed   Chemin du template détaillé (défaut : ./peppol_report_template.html.j2)
--history             Chemin du JSON d'historique (défaut : <output-dir>/peppol_history.json)
--detailed            Bascule en mode rapport complet
--sample-size, -n     Taille d'échantillon mode --detailed (max 1000)
--no-pdf              HTML seulement (saute WeasyPrint)
--no-api              Re-rend depuis l'historique existant sans interroger l'API
--author              Nom complet affiché dans le colophon
--verbose, -v         Logs détaillés
```

## Exemples

**Cron quotidien à 7h** (mode brief) :
```cron
0 7 * * * cd /path/to/peppol && python generate_peppol_report.py --output-dir ./out
```

**Rapport mensuel détaillé** (avec signatures + KPIs) :
```bash
python generate_peppol_report.py --detailed --output-dir ./monthly/2026-05
```

**Re-rendu uniquement** (utile pour itérer sur le template, sans re-frapper l'API) :
```bash
python generate_peppol_report.py --no-api
```

## Structure du JSON d'historique

```json
{
  "schema_version": 1,
  "runs": {
    "2026-05-24": {
      "fetched_at": "2026-05-24T11:14:00+02:00",
      "counts_fr": {
        "ubl_cius": 394561,
        "ubl_ext":  146561,
        "cii_cius": 394597,
        "cii_ext":  146462,
        "facturx":  377369,
        "cdar":     398783
      }
    },
    "2026-05-26": { ... }
  }
}
```

Une entrée par jour, clé = `YYYY-MM-DD`. Re-run dans la journée → écrasement (pas de doublons).

## Couleurs et styles SVG

Les courbes du panneau « volumes » utilisent des combinaisons couleur × style de trait pour distinguer 6 doctypes dans une palette homogène :

| Doctype | Couleur | Style |
|---|---|---|
| UBL CIUS | noir | solide |
| UBL EXT | rouge | solide gras |
| CII CIUS | noir | pointillé |
| CII EXT | rouge | pointillé gras |
| Factur-X | rouge sombre `#660000` | solide |
| CDAR | rouge moyen `#960000` | pointillé fin |

Le panneau « taux d'adoption » utilise une seule courbe rouge avec labels de valeur sur chaque point. Échelle Y adaptative cadrée sur ±2 points autour des valeurs observées.

## Limitations connues

- L'API plafonne les retours à 1 000 résultats par requête. Les chiffres totaux par doctype proviennent de `total-result-count` côté serveur (exhaustif), mais l'analyse de signatures (mode `--detailed`) porte sur les 1 000 premiers participants retournés, dans un ordre que l'API ne garantit pas aléatoire — les signatures observées peuvent donc être très stables d'un run à l'autre.
- Avec moins de 8 jours d'historique, la colonne « Δ J−7 » affiche « — ».
- Avec 1 seul run dans l'historique, les graphiques ne s'affichent pas (1 point ne fait pas une courbe). Le tableau d'évolution est aussi masqué.
- Le PASR France 2026.02.27 et ses obligations §6/§7 ne sont mentionnés que dans le bloc « Méthode » du rapport brief — pas de verdict éditorial sur la conformité.

## Lien officiel PASR

[France - Peppol Authority Specific Requirements_2026.02.27.pdf](https://openpeppol.atlassian.net/wiki/download/attachments/2889318401/France%20-%20Peppol%20Authority%20Specific%20Requirements_2026.02.27.pdf?api=v2)
