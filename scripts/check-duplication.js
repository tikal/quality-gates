#!/usr/bin/env node
'use strict';

const childProcess = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function fail(message) {
  process.stderr.write(`duplication gate: ${message}\n`);
  return 1;
}

function number(value, name) {
  if (!/^\d+$/.test(value)) {
    throw new Error(`${name} requires a non-negative integer`);
  }
  return Number(value);
}

function pattern(value, name) {
  try {
    return new RegExp(value);
  } catch (error) {
    throw new Error(`${name} has an invalid regular expression: ${error.message}`);
  }
}

function parseArguments(values) {
  const options = {
    all: [],
    commentPrefixes: ['#'],
    diffExclude: null,
    excludePrefixes: [],
    extension: null,
    format: '',
    ignores: [],
    minLines: 5,
    minTokens: 50,
    noReport: false,
    reporters: 'json',
    root: '',
    select: 'diff',
    strictScope: false,
    threshold: 5,
  };
  for (let index = 0; index < values.length; index += 1) {
    const name = values[index];
    if (name === '--no-report' || name === '--strict-scope') {
      options[name.slice(2).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = true;
      continue;
    }
    const value = values[index + 1];
    if (value === undefined) {
      throw new Error(`${name} requires a value`);
    }
    index += 1;
    if (name === '--root') options.root = path.resolve(value);
    else if (name === '--format') options.format = value;
    else if (name === '--ext') options.extension = pattern(value, name);
    else if (name === '--min-lines') options.minLines = number(value, name);
    else if (name === '--min-tokens') options.minTokens = number(value, name);
    else if (name === '--ignore') options.ignores.push(value);
    else if (name === '--diff-exclude') options.diffExclude = pattern(value, name);
    else if (name === '--select' && ['diff', 'tree'].includes(value)) options.select = value;
    else if (name === '--all') options.all.push(value.replaceAll('\\', '/'));
    else if (name === '--threshold') options.threshold = number(value, name);
    else if (name === '--reporters') options.reporters = value;
    else if (name === '--exclude-prefix') options.excludePrefixes.push(value.replaceAll('\\', '/'));
    else if (name === '--comment-prefix') options.commentPrefixes.push(value);
    else throw new Error(`unknown argument ${name}`);
  }
  if (!options.root || !options.format || options.extension === null) {
    throw new Error('--root, --format, and --ext are required');
  }
  return options;
}

function git(root, arguments_) {
  const environment = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_')));
  const result = childProcess.spawnSync('git', ['-C', root, ...arguments_], { encoding: 'utf8', env: environment });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(result.stderr.trim() || `git exited ${result.status}`);
  return result.stdout.split('\0').filter(Boolean);
}

function candidates(options) {
  if (options.select === 'tree') {
    return git(options.root, ['ls-files', '-z', '--cached', '--others', '--exclude-standard']);
  }
  return [...new Set([
    ...git(options.root, ['diff', '-z', '--name-only', '--diff-filter=ACM', 'HEAD']),
    ...git(options.root, ['diff', '-z', '--name-only', '--diff-filter=ACM', '--cached']),
  ])].sort();
}

function startsWithPath(file, prefix) {
  const normalized = prefix.replace(/\/+$/, '');
  if (normalized === '.' || normalized === '') return true;
  return file === normalized || file.startsWith(`${normalized}/`);
}

function globMatches(file, pattern_) {
  let expression = '';
  for (let index = 0; index < pattern_.length; index += 1) {
    const character = pattern_[index];
    if (character === '*') {
      expression += pattern_[index + 1] === '*' ? '.*' : '[^/]*';
      index += pattern_[index + 1] === '*' ? 1 : 0;
    } else {
      expression += /[|\\{}()[\]^$+?.]/.test(character) ? `\\${character}` : character;
    }
  }
  return new RegExp(`^${expression}$`).test(file);
}

function selected(options) {
  return candidates(options).filter((file) => {
    if (!fs.statSync(path.join(options.root, file), { throwIfNoEntry: false })?.isFile()) return false;
    if (!options.extension.test(file)) return false;
    if (options.excludePrefixes.some((prefix) => startsWithPath(file, prefix))) return false;
    if (options.ignores.some((pattern_) => globMatches(file, pattern_))) return false;
    if (options.select === 'diff' && options.diffExclude?.test(file)) return false;
    return options.all.length === 0 || options.all.some((target) => startsWithPath(file, target));
  });
}

function hasSource(root, file, prefixes) {
  const source = fs.readFileSync(path.join(root, file), 'utf8');
  const lines = source.split(/\r?\n/).filter((line) => {
    const trimmed = line.trimStart();
    return trimmed.length > 0 && !prefixes.some((prefix) => trimmed.startsWith(prefix));
  });
  if (lines.length === 0) return false;
  return !/^\s*(?:"""[\s\S]*?"""|'''[\s\S]*?''')\s*$/.test(lines.join('\n'));
}

function jscpdArguments(files, output, options) {
  const reporters = new Set(options.reporters.split(',').map((value) => value.trim()).filter(Boolean));
  reporters.add('json');
  return [
    ...files,
    '--min-lines', String(options.minLines),
    '--min-tokens', String(options.minTokens),
    '--format', options.format,
    '--threshold', String(options.threshold),
    '--reporters', [...reporters].sort().join(','),
    '--output', output,
    ...options.ignores.flatMap((pattern_) => ['--ignore', pattern_]),
  ];
}

function report(output) {
  const loaded = JSON.parse(fs.readFileSync(path.join(output, 'jscpd-report.json'), 'utf8'));
  const sources = loaded?.statistics?.total?.sources;
  if (!Number.isInteger(sources) || !Array.isArray(loaded.duplicates)) {
    throw new Error('jscpd report is incomplete');
  }
  return { clones: loaded.duplicates, sources };
}

function location(file) {
  return `${file?.name ?? 'unknown'}:${file?.start ?? '?'}`;
}

function printClones(clones) {
  if (clones.length === 0) return;
  process.stderr.write('Top duplication opportunities:\n');
  clones.sort((left, right) => right.lines - left.lines).slice(0, 10).forEach((clone) => {
    process.stderr.write(`  ${clone.lines} lines: ${location(clone.firstFile)} <-> ${location(clone.secondFile)}\n`);
  });
}

function runJscpd(files, options) {
  const output = fs.mkdtempSync(path.join(os.tmpdir(), 'jscpd-'));
  try {
    const result = childProcess.spawnSync('jscpd', jscpdArguments(files, output, options), { cwd: options.root, stdio: 'inherit' });
    const details = report(output);
    if (result.error) throw result.error;
    return { details, result };
  } finally {
    fs.rmSync(output, { force: true, recursive: true });
  }
}

function main() {
  let options;
  try {
    options = parseArguments(process.argv.slice(2));
    const files = selected(options);
    const sources = files.filter((file) => hasSource(options.root, file, options.commentPrefixes));
    if (files.length === 0 || sources.length === 0) return fail(`matched no source files under ${options.root}`);
    if (options.strictScope) {
      const scope = runJscpd(sources, { ...options, minLines: 1, minTokens: 1, reporters: 'json', threshold: 100 });
      if (scope.result.status !== 0) throw new Error(`jscpd coverage scan exited ${scope.result.status}`);
      if (scope.details.sources !== sources.length) {
        return fail(`jscpd read ${scope.details.sources} of ${sources.length} source files`);
      }
    }
    const { details, result } = runJscpd(files, options);
    if (result.error) throw result.error;
    if (result.status !== 0) {
      if (!options.noReport) printClones(details.clones);
      return fail(`failed over ${details.sources} files`);
    }
    process.stdout.write(`duplication gate clean scope=${options.strictScope ? sources.length : details.sources}\n`);
    return 0;
  } catch (error) {
    return fail(error.message);
  }
}

process.exitCode = main();
