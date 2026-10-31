# Part of FEC Check (Morocco). See LICENSE file for full copyright and licensing details.
"""Rules of the checks. Nothing here touches the database.

The rules follow opinion no. 24 of the Moroccan National Accounting Council (CNC, 14 June 2023): point 2 of its annex for the
criteria of accounting software, point 3 for the fields of the FEC. Odoo has no FEC export for Morocco, so the checks look at
the data these fields would be taken from:
- Num_Ecriture is account.move.name, and the numbering must have no gap inside a journal (point 2.2);
- Ref_Piece is the reference of the entry, or the number of the invoice for a customer invoice (point 2.4);
- Num_Compte_Aux is the partner, only on customer and supplier accounts;
- Num_Compte starts with three digits of the CGNC;
- Date_Validation is the closing date: Odoo holds it as a lock date.
"""
import datetime
import re

from odoo.tools.misc import file_open

MAX_SAMPLES = 20

# code: (section, severity)
CHECKS = {
    'C_COMPANY_ICE': ('settings', 'error'),
    'C_COMPANY_IF': ('settings', 'warning'),
    'C_CHART_NOT_MA': ('settings', 'error'),
    'C_KEY_ACCOUNT_MISSING': ('settings', 'error'),
    'C_ACCOUNT_TYPE': ('settings', 'error'),
    'C_TAX_ACCOUNT': ('settings', 'error'),
    'C_JOURNAL_BANK': ('settings', 'error'),
    'C_OPEN_PERIODS': ('settings', 'warning'),
    'C_YEAR_NOT_CLOSED': ('settings', 'info'),
    'L_EMPTY': ('ledger', 'info'),
    'L_UNBALANCED': ('ledger', 'error'),
    'L_NUM_MISSING': ('ledger', 'error'),
    'L_SEQUENCE_GAP': ('ledger', 'error'),
    'L_CHRONOLOGY': ('ledger', 'warning'),
    'L_REF_MISSING': ('ledger', 'error'),
    'L_LABEL_MISSING': ('ledger', 'warning'),
    'L_AUX_MISSING': ('ledger', 'error'),
    'L_ACCOUNT_NOT_IN_CGNC': ('ledger', 'warning'),
    'L_SALE_RECOVERABLE_VAT': ('ledger', 'error'),
    'L_PURCHASE_INVOICED_VAT': ('ledger', 'error'),
    'L_VAT_WITHOUT_BASE': ('ledger', 'warning'),
    'L_PIECE_DATE': ('ledger', 'warning'),
    'B_DRAFT_INVOICES': ('invoices', 'warning'),
    'B_DRAFT_ENTRIES': ('invoices', 'warning'),
    'B_SALE_BAD_CLASS': ('invoices', 'error'),
    'B_PURCHASE_BAD_CLASS': ('invoices', 'error'),
    'B_NO_TAX': ('invoices', 'warning'),
    'T_NO_REF': ('partners', 'warning'),
    'T_REF_DUPLICATE': ('partners', 'error'),
    'T_CHILD_PARTNER': ('partners', 'warning'),
    'T_NO_ICE': ('partners', 'warning'),
    'T_ICE_DUPLICATE': ('partners', 'warning'),
    'T_NO_COUNTRY': ('partners', 'warning'),
    'K_UNRECONCILED': ('bank', 'warning'),
    'K_SUSPENSE_BALANCE': ('bank', 'warning'),
    'K_OUTSTANDING': ('bank', 'warning'),
}

KEY_ACCOUNTS = {'4411': 'Suppliers', '3421': 'Customers', '3455': 'Recoverable VAT', '4455': 'Invoiced VAT', '514': 'Banks', '71': 'Sales'}
# Odoo gives these tax accounts the type receivable or payable, they are not customers or suppliers
TAX_PREFIXES = ('345', '445')
SALE_TYPES = ('out_invoice', 'out_refund', 'out_receipt')
PURCHASE_TYPES = ('in_invoice', 'in_refund', 'in_receipt')

_cgnc = None


def cgnc_accounts():
    """Headings of the Moroccan chart of accounts, as listed by the Odoo localization l10n_ma."""
    global _cgnc
    if _cgnc is None:
        accounts = set()
        with file_open('fec_check_ma/data/cgnc.csv', 'r') as source:
            for line in source:
                number = line.split(';', 1)[0]
                if number.isdigit():
                    accounts.add(number)
        _cgnc = accounts
    return _cgnc


def in_cgnc(code):
    """Opinion no. 24 asks that the first three characters are digits of the applicable chart. What follows is free."""
    code = str(code or '')
    # Class 8, the accounts of result, is in the CGNC but not in the chart of Odoo
    return bool(re.match(r'^\d{3}', code)) and (code[:3] in cgnc_accounts() or code.startswith('8'))


def ice_valid(ice):
    """Fifteen digits. Fifteen zeros is what some software writes when the ICE is unknown."""
    ice = (ice or '').strip()
    return bool(re.match(r'^\d{15}$', ice)) and set(ice) != {'0'}


def open_limit(today):
    """Last day that should be locked: only the current month and the previous one stay open."""
    previous = today.replace(day=1) - datetime.timedelta(days=1)
    return previous.replace(day=1) - datetime.timedelta(days=1)


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


def check_settings(results, company, accounts, journals, taxes, words):
    if company['country_code'] == 'MA':
        if not ice_valid(company['ice']):
            results.add('C_COMPANY_ICE', company['name'], company['ice'] or words['empty'], 'res.company', company['id'])
        if not (company['if'] or '').strip():
            results.add('C_COMPANY_IF', company['name'], words['empty'], 'res.company', company['id'])
    if company.get('chart_template') != 'ma':
        results.add('C_CHART_NOT_MA', company['name'], company.get('chart_template') or words['empty'])

    codes = [account['code'] for account in accounts.values()]
    for prefix, label in KEY_ACCOUNTS.items():
        if not any(code.startswith(prefix) for code in codes):
            results.add('C_KEY_ACCOUNT_MISSING', words[label], prefix)

    for account_id, account in accounts.items():
        code = account['code']
        kind = account['type']
        bad = ''
        first = code[:1]
        if first == '6' and not kind.startswith('expense'):
            bad = words['expense']
        elif first == '7' and not kind.startswith('income'):
            bad = words['income']
        elif code.startswith('3421') and kind != 'asset_receivable':
            bad = words['receivable']
        elif code.startswith('4411') and kind != 'liability_payable':
            bad = words['payable']
        elif kind == 'asset_receivable' and first != '3':
            bad = words['not_receivable']
        elif kind == 'liability_payable' and first != '4':
            bad = words['not_payable']
        if bad:
            results.add('C_ACCOUNT_TYPE', '%s %s' % (code, account['name']), '%s > %s' % (kind, bad), 'account.account', account_id)

    for tax in taxes:
        if tax['amount'] <= 0 or tax['use'] not in ('sale', 'purchase'):
            continue
        posted = 0
        for code, factor in tax['accounts']:
            if not code:
                # The non recoverable part of a tax has no account: it stays in the expense
                continue
            posted += 1
            wrong_sale = tax['use'] == 'sale' and not code.startswith('4455')
            # A negative share is the VAT due of a reverse charge
            wrong_purchase = tax['use'] == 'purchase' and not code.startswith('445' if factor < 0 else '3455')
            if wrong_sale or wrong_purchase:
                results.add('C_TAX_ACCOUNT', tax['name'], code, 'account.tax', tax['id'])
        if not posted:
            results.add('C_TAX_ACCOUNT', tax['name'], words['no_account'], 'account.tax', tax['id'])

    for journal_id, journal in journals.items():
        if journal['type'] not in ('bank', 'cash'):
            continue
        code = journal['account_code']
        expected = ('514', '554') if journal['type'] == 'bank' else ('516',)
        if not code.startswith(expected):
            results.add('C_JOURNAL_BANK', journal['name'], code or words['no_account'], 'account.journal', journal_id)


def check_entry(results, move, lines, accounts, partners):
    """Checks of one posted entry and its lines. An entry is always read with all its lines."""
    label = move['name'] or ''
    move_id = move['id']
    link = {'model': 'account.move', 'res_id': move_id}
    debit = credit = 0.0
    kind = move['move_type']
    sale = kind in SALE_TYPES
    purchase = kind in PURCHASE_TYPES
    invoiced = recoverable = base = settlement = no_tax = 0
    invoiced_code = ''
    invoiced_amount = 0.0

    for line in lines:
        debit += line['debit']
        credit += line['credit']
        amount = abs(line['debit'] - line['credit'])
        account_id = line['account_id']
        account = accounts.get(account_id, {'code': '', 'name': '', 'type': ''})
        code = account['code']
        partner_id = line['partner_id']
        collective = account['type'] in ('asset_receivable', 'liability_payable') and not code.startswith(TAX_PREFIXES)

        if collective and not partner_id:
            results.add('L_AUX_MISSING', label, code, amount=amount, distinct=move_id, **link)
        if collective and partner_id:
            partners.setdefault(partner_id, True)
        if not (line['name'] or '').strip():
            results.add('L_LABEL_MISSING', label, code, distinct=move_id, **link)
        if code and not in_cgnc(code):
            results.add('L_ACCOUNT_NOT_IN_CGNC', code, account['name'], 'account.account', account_id, amount, code)
        if code.startswith('3455'):
            recoverable += 1
            if move['journal_type'] == 'sale':
                results.add('L_SALE_RECOVERABLE_VAT', label, code, amount=amount, distinct=move_id, **link)
        if code.startswith('4455'):
            invoiced += 1
            invoiced_code = code
            invoiced_amount += amount
        if re.match(r'^(3456|4456|44551|34553|34554)', code):
            settlement += 1
        if re.match(r'^[267]', code):
            base += 1
        if line['display_type'] == 'product':
            if sale and not re.match(r'^(7|4421)', code):
                results.add('B_SALE_BAD_CLASS', label, '%s %s' % (code, account['name']), amount=amount, **link)
            # 31 is the stock of a company valuing it in real time
            if purchase and not re.match(r'^(6|2|31|3411)', code):
                results.add('B_PURCHASE_BAD_CLASS', label, '%s %s' % (code, account['name']), amount=amount, **link)
            if (sale or purchase) and not line['tax_ids']:
                no_tax += 1

    if abs(debit - credit) > 0.005:
        results.add('L_UNBALANCED', label, '%.2f' % (debit - credit), amount=abs(debit - credit), **link)
    if label in ('', '/'):
        results.add('L_NUM_MISSING', move['date'], move['journal_code'], **link)
    # The number of a customer invoice is its own document reference
    if kind not in SALE_TYPES and not (move['ref'] or '').strip():
        results.add('L_REF_MISSING', label, move['date'], **link)
    if no_tax:
        results.add('B_NO_TAX', label, no_tax, **link)
    if move['gap']:
        results.add('L_SEQUENCE_GAP', label, move['date'], **link)
    # With recoverable VAT on the same entry, invoiced VAT in a purchase is a reverse charge
    if move['journal_type'] == 'purchase' and invoiced and not recoverable:
        results.add('L_PURCHASE_INVOICED_VAT', label, invoiced_code, amount=invoiced_amount, **link)
    if kind == 'entry' and not move['cash_basis'] and (invoiced + recoverable) and not base and not settlement:
        results.add('L_VAT_WITHOUT_BASE', label, move['date'], **link)
    if move['invoice_date']:
        piece = datetime.date.fromisoformat(move['invoice_date'])
        entry = datetime.date.fromisoformat(move['date'])
        if piece > entry or (entry - piece).days > 366:
            results.add('L_PIECE_DATE', label, '%s / %s' % (move['invoice_date'], move['date']), **link)


def check_chronology(results, rows):
    """Rows sorted by journal, prefix and number: an entry must not be dated before the one numbered just before it."""
    previous = None
    for row in rows:
        key = (row['journal_id'], row['sequence_prefix'])
        if previous and previous[0] == key and row['date'] < previous[1]:
            results.add('L_CHRONOLOGY', row['name'], '%s < %s' % (row['date'], previous[1]), 'account.move', row['id'])
        previous = (key, row['date'])


def check_partners(results, rows, refs, ices):
    for row in rows:
        partner_id = row['id']
        name = row['name'] or ''
        link = {'model': 'res.partner', 'res_id': partner_id}
        ref = (row['ref'] or '').strip()
        ice = (row['company_registry'] or '').strip()
        country = row['country_code'] or ''
        commercial = row['commercial_partner_id'][0] if row['commercial_partner_id'] else partner_id

        if not ref:
            results.add('T_NO_REF', name, partner_id, **link)
        else:
            key = ref.upper()
            if key in refs and refs[key] != partner_id:
                results.add('T_REF_DUPLICATE', name, ref, **link)
            else:
                refs[key] = partner_id
        if commercial != partner_id:
            results.add('T_CHILD_PARTNER', name, row['commercial_partner_id'][1], **link)
        if not country:
            results.add('T_NO_COUNTRY', name, '', **link)
        if country == 'MA' and row['is_company'] and not ice_valid(ice):
            results.add('T_NO_ICE', name, ice, **link)
        if ice:
            if ice in ices and ices[ice] != commercial:
                results.add('T_ICE_DUPLICATE', name, ice, **link)
            else:
                ices[ice] = commercial


def check_periods(results, company, date_to, today, line_count, words):
    """Dates as ISO strings. The periods of the opinion are read as months: it does not define them."""
    if not line_count:
        return
    lock = company.get('lock_date') or ''
    limit = open_limit(datetime.date.fromisoformat(today)).isoformat()
    if lock < limit:
        results.add('C_OPEN_PERIODS', words['lock'], lock or words['no_lock'])
    if date_to < today and lock < date_to:
        results.add('C_YEAR_NOT_CLOSED', words['lock'], lock or words['no_lock'])


def finalize(results, tax_lines, line_count, words):
    if not tax_lines:
        # Company outside the VAT system: a line without tax is normal
        results.data['B_NO_TAX'] = {'count': 0, 'amount': 0.0, 'samples': [], 'seen': set()}
    if not line_count:
        results.add('L_EMPTY', words['empty_period'])
