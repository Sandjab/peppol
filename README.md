# Générateur du rapport PEPPOL France · adoption EXTENDED-CTC-FR

Pipeline reproductible qui interroge l'API Peppol Directory et produit deux
types de rapport :

- **Mode brief** (défaut) — comptages bruts du jour, table d'évolution
  (Δ J−1 / J−7 / origine), 2 graphiques SVG, et un encart **PASR D-Day**
  qui positionne l'adoption observée par rapport à la réforme CTC du
  01/09/2026 (J−N, comptage actuel vs univers TVA ~10 M, vélocité observée
  vs requise). Pensé pour un run quotidien automatisé.
- **Mode `--detailed`** — analyse complète avec KPIs, signatures de
  doctypes, échantillon d'entités. Inclut optionnellement (via
  `--enable-smp-lookup`) une section **Couverture par SMP** qui résout
  chaque participant via le SML Peppol puis agrège la palette de doctypes
  par domaine racine de SMP. **Désactivée par défaut** depuis la migration
  SML 2026 (deadline 31/08/2026) qui vide le SML pendant la transition —
  à réessayer une fois la migration finalisée.

Le rapport brief est publié quotidiennement sur
**[sandjab.github.io/peppol](https://sandjab.github.io/peppol)** via GitHub
Actions (cf. `.github/workflows/daily-report.yml`). Aucune action requise
côté lecteur — ce qui suit ne concerne que l'**install locale** pour
exécuter le script soi-même.

## Fichiers du bundle

| Fichier | Rôle |
|---|---|
| `generate_peppol_report.py` | Script CLI |
| `requirements.txt` | Dépendances Python |
| `peppol_report_brief.html.j2` | Template Jinja2 du rapport brief |
| `peppol_report_template.html.j2` | Template Jinja2 du rapport détaillé (mode `--detailed`, usage local) |
| `peppol_history.json` | Mémoire des runs (1 entrée par jour, JSON enrichi à chaque exécution) |

## Installation locale

Prérequis : **Python 3.11+** et `pip`. Sur Debian/Ubuntu, WeasyPrint a
besoin de quelques libs système pour le PDF :

```bash
sudo apt install libcairo2 libpango-1.0-0 libpangoft2-1.0-0
```

Puis, dans un virtualenv :

```bash
git clone https://github.com/Sandjab/peppol.git
cd peppol
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Si tu n'as pas besoin du PDF, tu peux te passer des libs système et lancer
le script avec `--no-pdf` — `weasyprint` reste dans `requirements.txt`
mais ne sera tout simplement pas appelé.

> **Windows** : `requirements.txt` installe automatiquement le package PyPI
> `tzdata`, indispensable car Windows ne fournit pas de base de fuseaux
> horaires système (sans lui, `ZoneInfo("Europe/Paris")` lève
> `ZoneInfoNotFoundError`). Sur Linux/macOS le tzdata système prime, le
> package n'est pas installé.

## Premier run

```bash
python generate_peppol_report.py
```

- Interroge l'API Peppol Directory pour les 6 doctypes France (~10 s).
- Ajoute / écrase l'entrée du jour dans `./out/peppol_history.json`.
- Génère `./out/peppol_extension_report.{html,pdf}` à partir de tout
  l'historique.

## Options CLI

```
--output-dir, -o      Répertoire de sortie (défaut : ./out)
--template-brief      Template brief    (défaut : ./peppol_report_brief.html.j2)
--template-detailed   Template détaillé (défaut : ./peppol_report_template.html.j2)
--history             JSON d'historique (défaut : <output-dir>/peppol_history.json)
--detailed            Bascule en mode rapport complet
--sample-size, -n     Taille d'échantillon mode --detailed (max 1000)
--no-pdf              HTML seulement (saute WeasyPrint)
--no-api              Re-rend depuis l'historique existant sans interroger l'API
--author              Nom complet affiché dans le colophon
--proxy               Proxy HTTP/HTTPS, format [scheme://]host[:port] (auth interactive)
--dns-doh             Mode --detailed : résout le SML via DNS-over-HTTPS
                      (dns.google) au lieu du resolver système. Utile derrière
                      un firewall qui filtre le DNS sortant. Suit --proxy.
--enable-smp-lookup   Mode --detailed : active la section « Couverture par SMP ».
                      Off par défaut pendant la migration SML Peppol 2026.
--verbose, -v         Logs détaillés
```

Codes de sortie : `0` OK · `2` échec collecte/historique · `3` HTML OK
mais PDF KO.

## Recettes courantes

**Run quotidien local (HTML+PDF dans `./out`) :**
```bash
python generate_peppol_report.py
```

**Cron quotidien (mode brief) :**
```cron
0 7 * * * cd /path/to/peppol && /path/to/.venv/bin/python generate_peppol_report.py --output-dir ./out
```

**Rapport mensuel détaillé** (utilise `peppol_report_template.html.j2`
versionné à la racine) :
```bash
python generate_peppol_report.py --detailed --output-dir ./monthly/2026-05
```

Le mode `--detailed` peut optionnellement inclure une **section
« Couverture par SMP »** (`--enable-smp-lookup`) : top 15 domaines de SMP
triés par participants observés, avec leur couverture des 6 doctypes
obligatoires PASR §6.1 (X/6) et le nombre de participants distincts par
doctype.

> **Note importante (mai 2026)** : la section SMP est désactivée par
> défaut. OpenPeppol a engagé fin 2025 une migration du SML (Service
> Metadata Locator) de la zone CEF eDelivery historique
> (`edelivery.tech.ec.europa.eu`) vers une zone in-house
> (`participant.sml.prod.tech.peppol.org`) avec deadlines d'enregistrement
> SMP au 31/05/2026 et de migration AP Lookup au 31/08/2026. Pendant la
> transition, le SML public est largement vide pour les participants
> français — la résolution renvoie 0 et la section ne sert à rien. À
> réessayer post-août 2026 avec `--enable-smp-lookup --dns-doh` derrière
> un proxy d'entreprise :
>
> ```bash
> python generate_peppol_report.py --detailed --no-pdf \
>     --enable-smp-lookup --dns-doh \
>     --proxy 10.38.253.65:8080
> ```

**Re-rendu sans appeler l'API** (utile pour itérer sur le template) :
```bash
python generate_peppol_report.py --no-api
```

**HTML seulement** (pas besoin de WeasyPrint ni des libs Cairo/Pango) :
```bash
python generate_peppol_report.py --no-pdf
```

**Derrière un proxy d'entreprise :**
```bash
python generate_peppol_report.py --proxy proxy.corp:8080
# le script demande user + password au prompt (password masqué)
# laisser le user vide si le proxy ne demande pas d'authentification
```

Pour un usage non-interactif (cron, CI) — passer les credentials via
variables d'environnement, le prompt est alors sauté :
```bash
export PEPPOL_PROXY_USER=alice
export PEPPOL_PROXY_PASS='s3cret!'
python generate_peppol_report.py --proxy http://proxy.corp:8080
```
Les credentials sont URL-encodés automatiquement, pas besoin d'échapper
les caractères spéciaux.

## Mode `--detailed` — note d'usage

Le template `peppol_report_template.html.j2` est versionné à la racine
du repo et destiné à un usage en local : la publication automatique
quotidienne se limite au brief. Lancer le mode détaillé via
`--detailed`, ou pointer vers un autre chemin avec `--template-detailed`.

Avec `--enable-smp-lookup`, le mode `--detailed` effectue ~6 000 lookups
DNS pour résoudre les participants vers leurs SMPs (top 15 affichés) —
compte ~1-2 min de résolution sur un réseau standard, plus en
environnement contraint. Sans le flag, le mode `--detailed` ne fait que
les 2 échantillons UBL CIUS + UBL EXT nécessaires aux signatures de
doctypes (~10-15 s en plus du brief).

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

Une entrée par jour, clé = `YYYY-MM-DD`. Re-run dans la journée →
écrasement (pas de doublons).

## Couleurs et styles SVG

Les courbes du panneau « volumes » utilisent des combinaisons couleur ×
style de trait pour distinguer 6 doctypes dans une palette homogène :

| Doctype | Couleur | Style |
|---|---|---|
| UBL CIUS | noir | solide |
| UBL EXT | rouge | solide gras |
| CII CIUS | noir | pointillé |
| CII EXT | rouge | pointillé gras |
| Factur-X | rouge sombre `#660000` | solide |
| CDAR | rouge moyen `#960000` | pointillé fin |

Le panneau « taux d'adoption » utilise une seule courbe rouge avec labels
de valeur sur chaque point. Échelle Y adaptative cadrée sur ±2 points
autour des valeurs observées.

## Limitations connues

- L'API plafonne les retours à 1 000 résultats par requête. Les chiffres
  totaux par doctype proviennent de `total-result-count` côté serveur
  (exhaustif), mais l'analyse de signatures (mode `--detailed`) porte sur
  les 1 000 premiers participants retournés, dans un ordre que l'API ne
  garantit pas aléatoire — les signatures observées peuvent donc être
  très stables d'un run à l'autre.
- Avec moins de 8 jours d'historique, la colonne « Δ J−7 » et la
  vélocité observée 7j de l'encart PASR affichent « — ».
- Avec 1 seul run dans l'historique, les graphiques ne s'affichent pas
  (1 point ne fait pas une courbe). Le tableau d'évolution est aussi
  masqué.
- L'indicateur « entités sur Peppol Directory FR » de l'encart PASR est
  une **borne basse** : c'est le max des 6 comptages doctypes, ce qui
  donne le nombre d'entités ayant déclaré ≥ 1 doctype. La vraie valeur
  est entre max() et sum() — typiquement très proche du max si les PA
  respectent le §6.1.
- La couverture par SMP utilise une heuristique eTLD+1 pour agréger les
  hostnames canoniques (`smp.docaposte.fr` → `docaposte.fr`). Les eTLDs
  multi-niveaux (`.co.uk`, `.com.fr`) peuvent être sur-agrégés. Suffisant
  pour la majorité des SMPs français (`.fr` / `.com` / `.eu`).
- Une cellule vide dans la table SMP signifie « aucun des participants
  de ce SMP **observés dans l'échantillon** n'a déclaré ce doctype » —
  c'est aussi une borne basse, le SMP peut servir d'autres clients hors
  sample avec ce format.

## Lien officiel PASR

[France - Peppol Authority Specific Requirements_2026.02.27.pdf](https://openpeppol.atlassian.net/wiki/download/attachments/2889318401/France%20-%20Peppol%20Authority%20Specific%20Requirements_2026.02.27.pdf?api=v2)
