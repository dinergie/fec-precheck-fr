# Part of FEC Pre-Export Check. See LICENSE file for full copyright and licensing details.
{
    'name': 'FEC Pre-Export Check (France)',
    'version': '18.0.1.0.1',
    'summary': 'FEC check: find the anomalies of French accounting data before the FEC export',
    'category': 'Accounting/Localizations',
    'author': 'DINERGIE',
    'license': 'LGPL-3',
    'depends': ['account'],
    'data': [
        'security/fec_precheck_security.xml',
        'security/ir.model.access.csv',
        'views/fec_precheck_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
}
