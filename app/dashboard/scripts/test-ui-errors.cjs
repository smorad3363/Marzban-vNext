const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
const locale = JSON.parse(fs.readFileSync(path.join(root, 'public/statics/locales/fa.json'), 'utf8'));
const source = fs.readFileSync(path.join(root, 'src/utils/apiError.ts'), 'utf8');
const exportsObject = {};
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
vm.runInNewContext(code, { exports: exportsObject, require: () => ({ default: { t: (key, args = {}) => locale[key] || args.defaultValue || key } }) });
const { localizedApiError, safeUserMessage } = exportsObject;
for (const detail of ['IntegrityError SQLAlchemy', 'خطا: SELECT password FROM admins', { code: 'internal_driver_crashed' }, { message_fa: 'خطا: pymysql.IntegrityError' }]) {
  const result = localizedApiError({ data: { detail } });
  assert.equal(result, locale['errors.fallback']);
}
assert.ok(localizedApiError({ data: { detail: { code: 'backup_missing_part', part: 3, total: 4 } } }).includes('3'));
assert.ok(!localizedApiError({ data: { detail: { code: 'plan_only_direct_edit_forbidden' } } }).includes('forbidden'));
assert.equal(safeUserMessage('نام کاربری تکراری است.'), 'نام کاربری تکراری است.');
assert.equal(safeUserMessage('Traceback: خطای داخلی'), null);
console.log('UI error mapping: 8 assertions passed');
