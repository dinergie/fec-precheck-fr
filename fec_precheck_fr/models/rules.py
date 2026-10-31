# Part of FEC Pre-Export Check. See LICENSE file for full copyright and licensing details.
"""Rules of the checks. Nothing here touches the database.

The rules follow what the export of Odoo really writes (l10n_fr_account, account_fr_fec_export_wizard.py):
- the SIREN of the file name comes from the VAT number, not from the company registry;
- PieceRef is account.move.ref, or "-" when it is empty;
- CompAuxNum is partner.ref, or the technical id of the partner, only on receivable and payable accounts;
- EcritureLib is "/" when the label of the line is empty;
- the result of the previous years goes to the first account of type equity_unaffected.
"""
import datetime
import re

from odoo.tools.misc import file_open

MAX_SAMPLES = 20

# code: (section, severity)
CHECKS = {
    'C_COMPANY_VAT': ('settings', 'error'),
    'C_CHART_NOT_FR': ('settings', 'error'),
    'C_KEY_ACCOUNT_MISSING': ('settings', 'error'),
    'C_ACCOUNT_TYPE': ('settings', 'error'),
    'C_UNAFFECTED_ACCOUNT': ('settings', 'warning'),
    'C_TAX_ACCOUNT': ('settings', 'error'),
    'C_TAX_OBSOLETE': ('settings', 'warning'),
    'C_JOURNAL_BANK': ('settings', 'error'),
    'C_LOCK_DATE': ('settings', 'info'),
    'L_EMPTY': ('ledger', 'info'),
    'L_UNBALANCED': ('ledger', 'error'),
    'L_NUM_MISSING': ('ledger', 'error'),
    'L_REF_MISSING': ('ledger', 'error'),
    'L_LABEL_MISSING': ('ledger', 'warning'),
    'L_AUX_MISSING': ('ledger', 'error'),
    'L_ACCOUNT_NOT_IN_PCG': ('ledger', 'warning'),
    'L_SALE_DEDUCTIBLE_VAT': ('ledger', 'error'),
    'L_PURCHASE_COLLECTED_VAT': ('ledger', 'error'),
    'L_VAT_WITHOUT_BASE': ('ledger', 'warning'),
    'L_PIECE_DATE': ('ledger', 'warning'),
    'B_DRAFT_INVOICES': ('invoices', 'warning'),
    'B_DRAFT_ENTRIES': ('invoices', 'warning'),
    'B_SALE_BAD_CLASS': ('invoices', 'error'),
    'B_PURCHASE_BAD_CLASS': ('invoices', 'error'),
    'B_NO_TAX': ('invoices', 'warning'),
    'B_SEQUENCE_GAP': ('invoices', 'warning'),
    'B_INTRA_SALE_VAT': ('invoices', 'error'),
    'B_INTRA_PURCHASE_NOT_REVERSE': ('invoices', 'warning'),
    'T_NO_REF': ('partners', 'warning'),
    'T_REF_DUPLICATE': ('partners', 'error'),
    'T_CHILD_PARTNER': ('partners', 'warning'),
    'T_VAT_DUPLICATE': ('partners', 'warning'),
    'T_NO_COUNTRY': ('partners', 'warning'),
    'T_EU_NO_VAT': ('partners', 'warning'),
    'K_UNRECONCILED': ('bank', 'warning'),
    'K_SUSPENSE_BALANCE': ('bank', 'warning'),
    'K_OUTSTANDING': ('bank', 'warning'),
}

EU_COUNTRIES = {'AT', 'BE', 'BG', 'CY', 'CZ', 'DE', 'DK', 'EE', 'ES', 'FI', 'GR', 'HR', 'HU', 'IE', 'IT', 'LT', 'LU', 'LV', 'MT', 'NL', 'PL', 'PT', 'RO', 'SE', 'SI', 'SK'}
KEY_ACCOUNTS = {'401': 'Suppliers', '411': 'Customers', '44566': 'Deductible VAT', '44571': 'Collected VAT', '512': 'Banks', '70': 'Sales'}
SALE_TYPES = ('out_invoice', 'out_refund', 'out_receipt')
PURCHASE_TYPES = ('in_invoice', 'in_refund', 'in_receipt')

_pcg = None


def pcg_accounts():
    """Accounts of the official chart: ANC regulation 2014-03, consolidated version of 1 January 2026."""
    global _pcg
    if _pcg is None:
        accounts = set()
        with file_open('fec_precheck_fr/data/pcg.csv', 'r') as source:
            for line in source:
                number = line.split(';', 1)[0]
                if number.isdigit():
                    accounts.add(number)
        _pcg = accounts
    return _pcg


def in_pcg(code):
    """The FEC requires that the first three characters are digits of the official chart. What follows is free: 401QINERA is valid."""
    code = str(code or '')
    if not re.match(r'^\d{3}', code):
        return False
    digits = re.match(r'^\d+', code).group(0)
    accounts = pcg_accounts()
    return any(digits[:length] in accounts for length in range(len(digits), 1, -1))


def siren_valid(siren):
    if not re.match(r'^\d{9}$', siren):
        return False
    total = 0
    for position, char in enumerate(siren):
        digit = int(char)
        if position % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


class Results:
    """Anomalies by check. Only the counters and a few examples are kept in memory."""

    def __init__(self):
        self.data = {code: {'count': 0, 'amount': 0.0, 'samples': [], 'seen': set()} for code in CHECKS}

    def add(self, code, label, value='', model=False, res_id=False, amount=0.0, distinct=None):
        result = self.data[code]
        result['count'] += 1
        result['amount'] += amount
        if distinct is not None:
            if distinct in result['seen']:
                return
            result['seen'].add(distinct)
        if len(result['samples']) < MAX_SAMPLES:
            result['samples'].append({'label': str(label), 'value': str(value), 'res_model': model or False, 'res_id': res_id or 0})

    def set_count(self, code, count):
        self.data[code]['count'] = count

    def count(self, code):
        return self.data[code]['count']


def check_settings(results, company, accounts, journals, taxes, date_from, words):
    vat = (company['vat'] or '').upper()
    if company['country_code'] == 'FR' and (len(vat) < 13 or not siren_valid(vat[4:13])):
        results.add('C_COMPANY_VAT', company['name'], vat or words['empty'], 'res.company', company['id'])
    if company.get('chart_template') and not str(company['chart_template']).startswith('fr'):
        results.add('C_CHART_NOT_FR', company['name'], company['chart_template'])

    codes = [account['code'] for account in accounts.values()]
    for prefix, label in KEY_ACCOUNTS.items():
        if not any(code.startswith(prefix) for code in codes):
            results.add('C_KEY_ACCOUNT_MISSING', words[label], prefix)

    unaffected = 0
    for account_id, account in accounts.items():
        code = account['code']
        kind = account['type']
        bad = ''
        first = code[:1]
        if kind == 'equity_unaffected':
            unaffected += 1
            if not code.startswith('12'):
                results.add('C_UNAFFECTED_ACCOUNT', account['name'], code, 'account.account', account_id)
        elif first == '6' and not kind.startswith('expense'):
            bad = words['expense']
        elif first == '7' and not kind.startswith('income'):
            bad = words['income']
        elif code.startswith('411') and kind != 'asset_receivable':
            bad = words['receivable']
        elif code.startswith('401') and kind != 'liability_payable':
            bad = words['payable']
        elif kind == 'asset_receivable' and first != '4':
            bad = words['not_receivable']
        elif kind == 'liability_payable' and first != '4':
            bad = words['not_payable']
        if bad:
            results.add('C_ACCOUNT_TYPE', '%s %s' % (code, account['name']), '%s > %s' % (kind, bad), 'account.account', account_id)
    if not unaffected and accounts:
        results.add('C_UNAFFECTED_ACCOUNT', words['no_unaffected'], '120/129')

    for tax in taxes:
        if tax['amount'] <= 0 or tax['use'] not in ('sale', 'purchase'):
            continue
        if abs(tax['amount'] - 19.6) < 0.001 or abs(tax['amount'] - 7.0) < 0.001:
            results.add('C_TAX_OBSOLETE', tax['name'], '%s %%' % tax['amount'], 'account.tax', tax['id'])
        posted = 0
        for code, factor in tax['accounts']:
            if not code:
                # The non deductible part of a tax has no account: it stays in the expense
                continue
            posted += 1
            wrong_sale = tax['use'] == 'sale' and not code.startswith('4457')
            # A negative share is the VAT due of a reverse charge
            wrong_purchase = tax['use'] == 'purchase' and not code.startswith('445' if factor < 0 else '4456')
            if wrong_sale or wrong_purchase:
                results.add('C_TAX_ACCOUNT', tax['name'], code, 'account.tax', tax['id'])
        if not posted:
            results.add('C_TAX_ACCOUNT', tax['name'], words['no_account'], 'account.tax', tax['id'])

    for journal_id, journal in journals.items():
        if journal['type'] not in ('bank', 'cash'):
            continue
        code = journal['account_code']
        expected = '512' if journal['type'] == 'bank' else '53'
        if not code.startswith(expected) and not code.startswith('51'):
            results.add('C_JOURNAL_BANK', journal['name'], code or words['no_account'], 'account.journal', journal_id)

    lock = company.get('fiscalyear_lock_date')
    if lock and lock >= date_from:
        results.add('C_LOCK_DATE', words['lock'], lock)


def check_entry(results, move, lines, accounts, partners):
    """Checks of one posted entry and its lines. An entry is always read with all its lines."""
    label = move['name'] or ''
    move_id = move['id']
    link = {'model': 'account.move', 'res_id': move_id}
    debit = credit = 0.0
    kind = move['move_type']
    sale = kind in SALE_TYPES
    purchase = kind in PURCHASE_TYPES
    collected = deductible = reverse = base = settlement = no_tax = 0

    for line in lines:
        debit += line['debit']
        credit += line['credit']
        amount = abs(line['debit'] - line['credit'])
        account_id = line['account_id']
        account = accounts.get(account_id, {'code': '', 'name': '', 'type': ''})
        code = account['code']
        partner_id = line['partner_id']
        collective = account['type'] in ('asset_receivable', 'liability_payable')

        if collective and not partner_id:
            results.add('L_AUX_MISSING', label, code, amount=amount, distinct=move_id, **link)
        if collective and partner_id:
            partners.setdefault(partner_id, {'s': 0, 'p': 0})
        if not (line['name'] or '').strip():
            results.add('L_LABEL_MISSING', label, code, distinct=move_id, **link)
        if code and not in_pcg(code):
            results.add('L_ACCOUNT_NOT_IN_PCG', code, account['name'], 'account.account', account_id, amount, code)
        if code.startswith('4456'):
            deductible += 1
            if move['journal_type'] == 'sale':
                results.add('L_SALE_DEDUCTIBLE_VAT', label, code, amount=amount, distinct=move_id, **link)
        if code.startswith('4457'):
            collected += 1
            if move['journal_type'] == 'purchase':
                results.add('L_PURCHASE_COLLECTED_VAT', label, code, amount=amount, distinct=move_id, **link)
        if re.match(r'^445[23]', code):
            reverse += 1
        if re.match(r'^(44551|44562|44567|44558|44583|4458)', code):
            settlement += 1
        if re.match(r'^[267]', code):
            base += 1
        if line['display_type'] == 'product':
            if sale and not re.match(r'^(7|419)', code):
                results.add('B_SALE_BAD_CLASS', label, '%s %s' % (code, account['name']), amount=amount, **link)
            if purchase and not re.match(r'^(6|2|409)', code):
                results.add('B_PURCHASE_BAD_CLASS', label, '%s %s' % (code, account['name']), amount=amount, **link)
            if (sale or purchase) and not line['tax_ids']:
                no_tax += 1

    if abs(debit - credit) > 0.005:
        results.add('L_UNBALANCED', label, '%.2f' % (debit - credit), amount=abs(debit - credit), **link)
    if label in ('', '/'):
        results.add('L_NUM_MISSING', move['date'], move['journal_code'], **link)
    if not (move['ref'] or '').strip():
        results.add('L_REF_MISSING', label, move['date'], **link)
    if no_tax:
        results.add('B_NO_TAX', label, no_tax, **link)
    if move['gap']:
        results.add('B_SEQUENCE_GAP', label, move['date'], **link)
    if kind == 'entry' and not move['cash_basis'] and (collected + deductible) and not base and not settlement:
        results.add('L_VAT_WITHOUT_BASE', label, move['date'], **link)
    if move['invoice_date']:
        piece = datetime.date.fromisoformat(move['invoice_date'])
        entry = datetime.date.fromisoformat(move['date'])
        if piece > entry or (entry - piece).days > 366:
            results.add('L_PIECE_DATE', label, '%s / %s' % (move['invoice_date'], move['date']), **link)
    usage = partners.get(move['partner_id'])
    if usage is not None:
        if sale and collected:
            usage['s'] += 1
            usage['sm'] = (move_id, label)
        if purchase and not reverse:
            usage['p'] += 1
            usage['pm'] = (move_id, label)


def check_partners(results, rows, used, refs, vats):
    for row in rows:
        partner_id = row['id']
        name = row['name'] or ''
        link = {'model': 'res.partner', 'res_id': partner_id}
        ref = (row['ref'] or '').strip()
        vat = re.sub(r'\s', '', row['vat'] or '').upper()
        country = row['country_code'] or ''
        commercial = row['commercial_partner_id'][0] if row['commercial_partner_id'] else partner_id

        if not ref:
            results.add('T_NO_REF', name, 'CompAuxNum = %s' % partner_id, **link)
        else:
            key = ref.upper()
            if key in refs and refs[key] != partner_id:
                results.add('T_REF_DUPLICATE', name, ref, **link)
            else:
                refs[key] = partner_id
        if commercial != partner_id:
            results.add('T_CHILD_PARTNER', name, row['commercial_partner_id'][1], **link)
        if vat:
            if vat in vats and vats[vat] != commercial:
                results.add('T_VAT_DUPLICATE', name, vat, **link)
            else:
                vats[vat] = commercial
        if not country:
            results.add('T_NO_COUNTRY', name, '', **link)
        if country in EU_COUNTRIES:
            if not vat and row['is_company']:
                results.add('T_EU_NO_VAT', name, country, **link)
            usage = used.get(partner_id, {'s': 0, 'p': 0})
            if usage['s'] and vat:
                results.add('B_INTRA_SALE_VAT', name, '%s (%s)' % (usage['sm'][1], usage['s']), 'account.move', usage['sm'][0])
            if usage['p'] and vat:
                results.add('B_INTRA_PURCHASE_NOT_REVERSE', name, '%s (%s)' % (usage['pm'][1], usage['p']), 'account.move', usage['pm'][0])


def finalize(results, tax_lines, line_count, words):
    if not tax_lines:
        # Company outside the VAT system: a line without tax is normal
        results.data['B_NO_TAX'] = {'count': 0, 'amount': 0.0, 'samples': [], 'seen': set()}
    if not line_count:
        results.add('L_EMPTY', words['empty_period'])
