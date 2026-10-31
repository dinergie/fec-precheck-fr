# Part of FEC Check (Morocco). See LICENSE file for full copyright and licensing details.
{
    'name': 'FEC Check (Morocco)',
    'version': '19.0.1.0.0',
    'summary': 'FEC check for Morocco: find the anomalies of CGNC accounting data against CNC opinion no. 24',
    'category': 'Accounting/Localizations',
    'author': 'Cabinet EL JOUAIDI',
    'license': 'LGPL-3',
    'depends': ['l10n_ma'],
    'data': [
        'security/fec_check_security.xml',
        'security/ir.model.access.csv',
        'views/fec_check_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
}
