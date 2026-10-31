# Part of FEC Check (Morocco). See LICENSE file for full copyright and licensing details.
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import rules

MOVES_PER_BATCH = 500
INVOICE_TYPES = ('out_invoice', 'out_refund', 'in_invoice', 'in_refund', 'out_receipt', 'in_receipt')


class FecCheckMaRun(models.Model):
    _name = 'fec.check.ma.run'
    _description = 'FEC check (Morocco)'
    _order = 'id desc'
    _rec_name = 'display_label'

    company_id = fields.Many2one('res.company', string='Company', required=True, readonly=True, default=lambda self: self.env.company)
    date_from = fields.Date(string='Start Date', required=True, default=lambda self: self._default_dates()[0])
    date_to = fields.Date(string='End Date', required=True, default=lambda self: self._default_dates()[1])
    state = fields.Selection([('draft', 'To run'), ('done', 'Done')], string='Status', default='draft', required=True, readonly=True)
    run_date = fields.Datetime(string='Checked on', readonly=True)
    line_count = fields.Integer(string='Lines checked', readonly=True)
    error_count = fields.Integer(string='Errors', readonly=True)
    warning_count = fields.Integer(string='To review', readonly=True)
    passed_count = fields.Integer(string='Checks passed', readonly=True)
    finding_ids = fields.One2many('fec.check.ma.finding', 'run_id', string='Anomalies', readonly=True)
    display_label = fields.Char(compute='_compute_display_label')

    @api.model
    def _default_dates(self):
        dates = self.env.company.compute_fiscalyear_dates(fields.Date.context_today(self))
        return dates['date_from'], dates['date_to']

    @api.depends('company_id', 'date_from', 'date_to')
    def _compute_display_label(self):
        for run in self:
            run.display_label = '%s %s - %s' % (run.company_id.name or '', run.date_from or '', run.date_to or '')

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for run in self:
            if run.date_from > run.date_to:
                raise UserError(_('The start date must be before the end date.'))

    def _words(self):
        return {
            'empty': _('empty'),
            'Suppliers': _('Suppliers'),
            'Customers': _('Customers'),
            'Recoverable VAT': _('Recoverable VAT'),
            'Invoiced VAT': _('Invoiced VAT'),
            'Banks': _('Banks'),
            'Sales': _('Sales'),
            'expense': _('expense'),
            'income': _('income'),
            'receivable': _('receivable'),
            'payable': _('payable'),
            'not_receivable': _('other than receivable'),
            'not_payable': _('other than payable'),
            'no_account': _('no account'),
            'lock': _('Lock date for all entries'),
            'no_lock': _('no lock date'),
            'empty_period': _('No entry in this period'),
        }

    def _company_ids(self):
        return self.company_id._accessible_branches().ids

    def action_run(self):
        """Read the accounting data and record the anomalies. The only records written are those of this module."""
        self.ensure_one()
        run = self.with_company(self.company_id).with_context(allowed_company_ids=self.company_id._accessible_branches().ids)
        words = run._words()
        results = rules.Results()
        accounts, journals = run._check_settings(results, words)
        partners = {}
        line_count, tax_lines = run._check_entries(results, accounts, journals, partners)
        run._check_chronology(results)
        run._check_drafts(results)
        run._check_bank(results, accounts)
        run._check_partners(results, partners)
        run._check_periods(results, line_count, words)
        rules.finalize(results, tax_lines, line_count, words)
        run._save(results, line_count)
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def _check_settings(self, results, words):
        company = self.company_id
        Account = self.env['account.account']
        accounts = {}
        for row in Account.search_read(Account._check_company_domain(company), ['code', 'name', 'account_type', 'reconcile'], load=False):
            accounts[row['id']] = {'code': row['code'] or '', 'name': row['name'] or '', 'type': row['account_type'] or '', 'reconcile': row['reconcile']}

        company_ids = self._company_ids()
        journals = {}
        for row in self.env['account.journal'].search_read([('company_id', 'in', company_ids)], ['name', 'code', 'type', 'default_account_id'], load=False):
            account = accounts.get(row['default_account_id'], {'code': ''})
            journals[row['id']] = {'name': row['name'], 'code': row['code'], 'type': row['type'], 'account_code': account['code']}

        taxes = {}
        for row in self.env['account.tax'].search_read([('company_id', 'in', company_ids), ('amount_type', '=', 'percent')], ['name', 'amount', 'type_tax_use'], load=False):
            taxes[row['id']] = {'id': row['id'], 'name': row['name'], 'amount': row['amount'], 'use': row['type_tax_use'], 'accounts': []}
        if taxes:
            domain = [('tax_id', 'in', list(taxes)), ('repartition_type', '=', 'tax'), ('document_type', '=', 'invoice')]
            for row in self.env['account.tax.repartition.line'].search_read(domain, ['tax_id', 'account_id', 'factor_percent'], load=False):
                if row['tax_id'] in taxes:
                    account = accounts.get(row['account_id'], {'code': ''})
                    taxes[row['tax_id']]['accounts'].append((account['code'], row['factor_percent']))

        values = {
            'id': company.id,
            'name': company.name,
            'ice': company.company_registry,
            'if': company.vat,
            'country_code': company.country_code,
            'chart_template': company.chart_template,
        }
        rules.check_settings(results, values, accounts, journals, list(taxes.values()), words)
        return accounts, journals

    def _moves_domain(self, state='posted'):
        return [('company_id', 'in', self._company_ids()), ('state', '=', state), ('date', '>=', self.date_from), ('date', '<=', self.date_to)]

    def _check_entries(self, results, accounts, journals, partners):
        Move = self.env['account.move']
        Line = self.env['account.move.line']
        cursor = 0
        line_count = tax_lines = 0
        move_fields = ['name', 'ref', 'date', 'invoice_date', 'move_type', 'journal_id', 'made_sequence_gap', 'tax_cash_basis_origin_move_id']
        line_fields = ['move_id', 'account_id', 'partner_id', 'name', 'debit', 'credit', 'display_type', 'tax_ids', 'tax_line_id']
        while True:
            moves = Move.search_read(self._moves_domain() + [('id', '>', cursor)], move_fields, limit=MOVES_PER_BATCH, order='id', load=False)
            if not moves:
                break
            cursor = moves[-1]['id']
            by_move = {}
            domain = [('move_id', 'in', [move['id'] for move in moves]), ('display_type', 'not in', ('line_section', 'line_note'))]
            for line in Line.search_read(domain, line_fields, order='id', load=False):
                by_move.setdefault(line['move_id'], []).append(line)
                line_count += 1
                if line['tax_line_id']:
                    tax_lines += 1
            for move in moves:
                journal = journals.get(move['journal_id'], {'type': '', 'code': ''})
                entry = {
                    'id': move['id'],
                    'name': move['name'] or '',
                    'ref': move['ref'] or '',
                    'date': fields.Date.to_string(move['date']),
                    'invoice_date': fields.Date.to_string(move['invoice_date']) if move['invoice_date'] else '',
                    'move_type': move['move_type'],
                    'journal_type': journal['type'],
                    'journal_code': journal['code'],
                    'gap': move['made_sequence_gap'],
                    'cash_basis': bool(move['tax_cash_basis_origin_move_id']),
                }
                rules.check_entry(results, entry, by_move.get(move['id'], []), accounts, partners)
            # The cache of a large fiscal year must not stay in memory
            self.env.invalidate_all()
            if len(moves) < MOVES_PER_BATCH:
                break
        return line_count, tax_lines

    def _check_chronology(self, results):
        rows = self.env['account.move'].search_read(
            self._moves_domain() + [('sequence_number', '>', 0)],
            ['name', 'date', 'journal_id', 'sequence_prefix'],
            order='journal_id, sequence_prefix, sequence_number',
            load=False,
        )
        rules.check_chronology(results, rows)

    def _check_drafts(self, results):
        Move = self.env['account.move']
        cases = {'B_DRAFT_INVOICES': [('move_type', 'in', INVOICE_TYPES)], 'B_DRAFT_ENTRIES': [('move_type', '=', 'entry')]}
        for code, condition in cases.items():
            domain = self._moves_domain('draft') + condition
            count = Move.search_count(domain)
            if not count:
                continue
            for move in Move.search(domain, limit=rules.MAX_SAMPLES, order='date, id'):
                name = move.name if move.name and move.name != '/' else (move.ref or _('Draft'))
                value = '%s %s' % (move.date, move.partner_id.display_name or '')
                results.add(code, name, value, 'account.move', move.id, abs(move.amount_total_signed))
            results.set_count(code, count)

    def _check_bank(self, results, accounts):
        company_ids = self._company_ids()
        StatementLine = self.env['account.bank.statement.line']
        Line = self.env['account.move.line']
        domain = [('company_id', 'in', company_ids), ('is_reconciled', '=', False), ('date', '>=', self.date_from), ('date', '<=', self.date_to)]
        count = StatementLine.search_count(domain)
        if count:
            for statement in StatementLine.search(domain, limit=2000, order='date, id'):
                results.add('K_UNRECONCILED', '%s %s' % (statement.date, statement.payment_ref or ''), '%.2f' % statement.amount, 'account.move', statement.move_id.id, abs(statement.amount))
            results.set_count('K_UNRECONCILED', count)

        suspense = self.env['account.journal'].search([('company_id', 'in', company_ids), ('type', 'in', ('bank', 'cash'))]).suspense_account_id
        for account in suspense:
            domain = [('account_id', '=', account.id), ('parent_state', '=', 'posted'), ('date', '<=', self.date_to), ('company_id', 'in', company_ids)]
            balance = Line._read_group(domain, [], ['balance:sum'])[0][0] or 0.0
            if abs(balance) > 0.005:
                results.add('K_SUSPENSE_BALANCE', account.code, '%.2f' % balance, 'account.account', account.id, abs(balance))

        outstanding = [account_id for account_id, account in accounts.items() if account['reconcile'] and account['code'].startswith('511')]
        if outstanding:
            domain = [('account_id', 'in', outstanding), ('parent_state', '=', 'posted'), ('date', '<=', self.date_to), ('reconciled', '=', False), ('company_id', 'in', company_ids)]
            count = Line.search_count(domain)
            if count:
                for line in Line.search(domain, limit=rules.MAX_SAMPLES, order='date, id'):
                    results.add('K_OUTSTANDING', '%s %s' % (line.date, line.move_id.display_name), '%.2f' % line.balance, 'account.move', line.move_id.id, abs(line.balance))
                results.set_count('K_OUTSTANDING', count)

    def _check_partners(self, results, partners):
        Partner = self.env['res.partner'].with_context(active_test=False)
        refs = {}
        ices = {}
        ids = list(partners)
        names = ['name', 'ref', 'company_registry', 'country_code', 'commercial_partner_id', 'is_company']
        for start in range(0, len(ids), 500):
            rows = Partner.search_read([('id', 'in', ids[start:start + 500])], names)
            rules.check_partners(results, rows, refs, ices)

    def _check_periods(self, results, line_count, words):
        company = self.company_id
        locks = [company[name] for name in ('fiscalyear_lock_date', 'hard_lock_date') if name in company._fields and company[name]]
        values = {'lock_date': fields.Date.to_string(max(locks)) if locks else ''}
        today = fields.Date.to_string(fields.Date.context_today(self))
        rules.check_periods(results, values, fields.Date.to_string(self.date_to), today, line_count, words)

    def _save(self, results, line_count):
        self.finding_ids.unlink()
        counts = {'error': 0, 'warning': 0, 'info': 0, 'passed': 0}
        values = []
        for code, (section, severity) in rules.CHECKS.items():
            result = results.data[code]
            if not result['count']:
                counts['passed'] += 1
                continue
            counts[severity] += 1
            values.append({
                'run_id': self.id,
                'code': code,
                'section': section,
                'severity': severity,
                'count': result['count'],
                'amount': result['amount'],
                'sample_ids': [(0, 0, sample) for sample in result['samples']],
            })
        self.env['fec.check.ma.finding'].create(values)
        self.write({
            'state': 'done',
            'run_date': fields.Datetime.now(),
            'line_count': line_count,
            'error_count': counts['error'],
            'warning_count': counts['warning'],
            'passed_count': counts['passed'],
        })


class FecCheckMaFinding(models.Model):
    _name = 'fec.check.ma.finding'
    _description = 'Anomaly of a FEC check (Morocco)'
    _order = 'severity_rank, id'
    _rec_name = 'title'

    run_id = fields.Many2one('fec.check.ma.run', string='Run', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='run_id.company_id', store=True)
    code = fields.Char(string='Code', required=True, readonly=True)
    section = fields.Selection([
        ('settings', 'Settings'),
        ('ledger', 'Entries'),
        ('invoices', 'Invoices'),
        ('partners', 'Partners'),
        ('bank', 'Bank'),
    ], string='Section', required=True, readonly=True)
    severity = fields.Selection([('error', 'Error'), ('warning', 'To review'), ('info', 'Information')], string='Severity', required=True, readonly=True)
    severity_rank = fields.Integer(compute='_compute_severity_rank', store=True)
    count = fields.Integer(string='Number', readonly=True)
    amount = fields.Float(string='Amount', readonly=True, digits=(16, 2))
    title = fields.Char(string='Check', compute='_compute_texts')
    advice = fields.Text(string='What to do', compute='_compute_texts')
    sample_ids = fields.One2many('fec.check.ma.sample', 'finding_id', string='Examples', readonly=True)

    @api.depends('severity')
    def _compute_severity_rank(self):
        ranks = {'error': 0, 'warning': 1, 'info': 2}
        for finding in self:
            finding.severity_rank = ranks.get(finding.severity, 3)

    def _texts(self):
        return {
            'C_COMPANY_ICE': (_('ICE of the company missing or invalid'), _('The ICE has 15 digits. Fill it in on the company form.')),
            'C_COMPANY_IF': (_('Tax identifier of the company missing'), _('Fill in the tax identifier (IF) on the company form.')),
            'C_CHART_NOT_MA': (_('The company does not use the Moroccan chart of accounts'), _('Install the Morocco localization and select the Morocco chart of accounts in the accounting settings.')),
            'C_KEY_ACCOUNT_MISSING': (_('Required account missing from the chart'), _('Create the account in Accounting > Configuration > Chart of Accounts.')),
            'C_ACCOUNT_TYPE': (_('Account type inconsistent with its number'), _('Fix the type on the account form: it decides whether the account is followed by partner, and the balance sheet classification.')),
            'C_TAX_ACCOUNT': (_('Active tax linked to a wrong VAT account'), _('In the distribution of the tax, use a 4455 account for sales and a 3455 account for purchases.')),
            'C_JOURNAL_BANK': (_('Bank or cash journal linked to a wrong account'), _('The journal must point to a 514 account for a bank, and a 516 account for cash.')),
            'C_OPEN_PERIODS': (_('More than two periods open'), _('Opinion no. 24 allows only the current period and the previous one to be open. Read as months, the entries before the previous month should be locked: set the lock date.')),
            'C_YEAR_NOT_CLOSED': (_('Period over but not locked'), _('The Date_Validation field of the FEC is the date the entries become final. Set the lock date once the period is closed.')),
            'L_EMPTY': (_('No posted entry in this period'), _('Check the period.')),
            'L_UNBALANCED': (_('Unbalanced entry'), _('Total debits must equal total credits: reset the entry to draft to fix it.')),
            'L_NUM_MISSING': (_('Posted entry without a number'), _('Num_Ecriture would be empty in the FEC: the journal must assign a sequence.')),
            'L_SEQUENCE_GAP': (_('Gap in the numbering of a journal'), _('Opinion no. 24 requires a numbering with no gap and no duplicate in each journal. Identify the missing entry.')),
            'L_CHRONOLOGY': (_('Entry dated before the entry numbered just before it'), _('Opinion no. 24 rules out any insertion between recorded entries. Check the date of the entry.')),
            'L_REF_MISSING': (_('Entry without a document reference'), _('Opinion no. 24 requires the reference of the supporting document on each entry (Ref_Piece). Fill in the Reference field.')),
            'L_LABEL_MISSING': (_('Line without a label'), _('Libelle_Ecriture would be empty in the FEC. Fill in the label of the line.')),
            'L_AUX_MISSING': (_('Customer or supplier account line without a partner'), _('The totals of the individual accounts must match the collective accounts (point 2.5 of opinion no. 24). Set the partner on the line.')),
            'L_ACCOUNT_NOT_IN_CGNC': (_('Account outside the CGNC'), _('The first three characters of Num_Compte must be digits of the applicable chart. Renumber the account.')),
            'L_SALE_RECOVERABLE_VAT': (_('Recoverable VAT in the sales journal'), _('A sale carries invoiced VAT (4455). Check the tax used.')),
            'L_PURCHASE_INVOICED_VAT': (_('Invoiced VAT in the purchase journal'), _('A purchase carries recoverable VAT (3455), except for a reverse charge. Check the tax used.')),
            'L_VAT_WITHOUT_BASE': (_('VAT entry without an expense, income or fixed asset account'), _('Apart from a VAT return, a VAT line comes with a base. Check the entry.')),
            'L_PIECE_DATE': (_('Document date after the accounting date, or more than one year away'), _('Check the invoice date and the accounting date.')),
            'B_DRAFT_INVOICES': (_('Draft invoices in the period'), _('A draft is not a recorded entry. Post or delete these invoices before closing the period.')),
            'B_DRAFT_ENTRIES': (_('Draft journal entries in the period'), _('A draft is not a recorded entry. Post or delete these entries before closing the period.')),
            'B_SALE_BAD_CLASS': (_('Sales invoice line outside class 7'), _('A sale is booked in class 7. Fix the account of the line or of the product.')),
            'B_PURCHASE_BAD_CLASS': (_('Vendor bill line outside classes 6 and 2'), _('A purchase is booked in class 6, or class 2 for a fixed asset. Fix the account of the line or of the product.')),
            'B_NO_TAX': (_('Invoice line without a tax'), _('Set the applicable tax, even at 0%: Odoo uses it for the VAT return.')),
            'T_NO_REF': (_('Partner without a reference'), _('Num_Compte_Aux needs a stable code. Fill in the Reference field of the partner.')),
            'T_REF_DUPLICATE': (_('Same reference on several partners'), _('Two partners would share the same subledger account. Give each partner its own reference.')),
            'T_CHILD_PARTNER': (_('Entries on a contact rather than on its company'), _('The same company would have several subledger accounts. Book the entries on the company.')),
            'T_NO_ICE': (_('Moroccan company without a valid ICE'), _('The ICE has 15 digits. Fill it in on the partner form.')),
            'T_ICE_DUPLICATE': (_('Same ICE on several partners'), _('Probably a duplicate: merge the partners.')),
            'T_NO_COUNTRY': (_('Partner without a country'), _('Without a country, Odoo can not determine the VAT treatment.')),
            'K_UNRECONCILED': (_('Bank statement lines not reconciled'), _('As long as a line is not reconciled, it stays on the suspense account. Reconcile it.')),
            'K_SUSPENSE_BALANCE': (_('Suspense account not cleared at the end of the period'), _('Clear the suspense account by reconciling the statement lines concerned.')),
            'K_OUTSTANDING': (_('Payments waiting for reconciliation'), _('These amounts stay on a 511 account: reconcile them with the bank statement.')),
        }

    def _compute_texts(self):
        texts = self._texts()
        for finding in self:
            finding.title, finding.advice = texts.get(finding.code, (finding.code, ''))


class FecCheckMaSample(models.Model):
    _name = 'fec.check.ma.sample'
    _description = 'Example of an anomaly (Morocco)'
    _order = 'id'
    _rec_name = 'label'

    finding_id = fields.Many2one('fec.check.ma.finding', string='Anomaly', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='finding_id.company_id', store=True)
    label = fields.Char(string='Item', readonly=True)
    value = fields.Char(string='Value', readonly=True)
    res_model = fields.Char(readonly=True)
    res_id = fields.Integer(readonly=True)
    can_open = fields.Boolean(compute='_compute_can_open')

    @api.depends('res_model', 'res_id')
    def _compute_can_open(self):
        for sample in self:
            sample.can_open = bool(sample.res_model and sample.res_id)

    def action_open(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': self.res_model, 'res_id': self.res_id, 'view_mode': 'form', 'views': [(False, 'form')], 'target': 'current'}
