# Odoo apps

Free Odoo modules that check accounting data before the FEC (Fichier des Écritures Comptables).

They run read-only checks on settings, entries, invoices, partners and bank, and tell what to do for each anomaly.
They change no accounting data, send nothing outside the database and need no activation key.

| Module | Country | Checks | Rules |
|---|---|---|---|
| `fec_precheck_fr` | France | 37 | What the FEC export of Odoo really writes, French general chart of accounts |
| `fec_check_ma` | Morocco | 36 | CNC opinion no. 24 (June 2023), CGNC |

- Branches: `18.0`, `19.0`
- License: LGPL-3
- Authors: DINERGIE (France), Cabinet EL JOUAIDI (Morocco)

They work on Odoo.sh and on your own servers, not on Odoo Online.
