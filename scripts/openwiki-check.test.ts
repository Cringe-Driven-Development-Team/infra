import { afterAll, expect, test } from 'bun:test';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { checkOpenWiki } from './openwiki-check.ts';

const roots: string[] = [];
const script = resolve(import.meta.dir, 'openwiki-check.ts');

afterAll(() => {
    for (const root of roots) rmSync(root, { recursive: true, force: true });
});

function git(root: string, ...args: string[]): string {
    const result = Bun.spawnSync(['git', ...args], { cwd: root });
    if (result.exitCode !== 0) throw new Error(result.stderr.toString());
    return result.stdout.toString().trim();
}

function write(root: string, path: string, contents: string) {
    mkdirSync(dirname(join(root, path)), { recursive: true });
    writeFileSync(join(root, path), contents);
}

function commit(root: string): string {
    git(root, 'add', '--all');
    git(root, 'commit', '-qm', 'fixture');
    return git(root, 'rev-parse', 'HEAD');
}

function fixture() {
    const root = mkdtempSync(join(tmpdir(), 'infra openwiki '));
    roots.push(root);
    git(root, 'init', '-q');
    git(root, 'config', 'user.name', 'Wiki Test');
    git(root, 'config', 'user.email', 'wiki@example.invalid');
    write(root, 'pulumi/index.ts', 'source\n');
    write(root, 'AGENTS.md', 'instructions\n');
    write(root, '.github/workflows/infra.yml', 'workflow\n');
    const sourceSha = commit(root);
    checkpoint(root, {
        gitHead: sourceSha,
        status: 'complete',
        updatedAt: '2026-10-24T00:00:00.000Z',
    });
    write(root, 'openwiki/quickstart.md', '# Wiki\n');
    const targetSha = commit(root);
    return { root, sourceSha, targetSha };
}

function checkpoint(root: string, value: unknown) {
    write(root, 'openwiki/.last-update.json', JSON.stringify(value));
}

function cli(root: string, ...args: string[]) {
    return Bun.spawnSync([process.execPath, script, ...args], { cwd: root });
}

test('complete ancestor checkpoint stays fresh after a wiki-only commit', async () => {
    const { root, sourceSha, targetSha } = fixture();
    expect(await checkOpenWiki(root, targetSha)).toEqual({
        sourceSha,
        targetSha,
        changedPaths: [],
        fresh: true,
    });
});

test('checkpoint at the target commit is fresh regardless of its age', async () => {
    const { root, sourceSha } = fixture();
    checkpoint(root, { gitHead: sourceSha, status: 'complete', updatedAt: '2000-01-01T00:00:00Z' });
    expect((await checkOpenWiki(root, sourceSha)).fresh).toBe(true);
});

const sources = [
    'pulumi/index.ts', 'pulumi/bootstrap/index.ts', 'pulumi/selectel-storage.test.ts',
    'ansible/site.yml', 'ansible/roles/app/templates/compose.yml.j2',
    'ansible/roles/app/templates/env.j2', 'ansible/runtime/site.yml',
    '.github/workflows/infra.yml', 'SUMMARY.md', 'AGENTS.md',
    '.openwikiignore', 'openwiki/INSTRUCTIONS.md',
];
for (const path of sources) {
    test(`tracked source change in ${path} makes the wiki stale`, async () => {
        const { root, sourceSha } = fixture();
        write(root, path, 'changed\n');
        const targetSha = commit(root);
        expect(await checkOpenWiki(root, targetSha)).toEqual({
            sourceSha,
            targetSha,
            changedPaths: [path],
            fresh: false,
        });
    });
}

test('NUL-delimited paths preserve spaces, newlines, deleted sources and source-to-wiki moves', async () => {
    const { root } = fixture();
    write(root, 'pulumi/space and\nnewline.ts', 'new\n');
    git(root, 'mv', 'pulumi/index.ts', 'openwiki/moved.md');
    const targetSha = commit(root);
    expect((await checkOpenWiki(root, targetSha)).changedPaths).toEqual([
        'pulumi/index.ts',
        'pulumi/space and\nnewline.ts',
    ]);
});

test('the explicit target is used even when the checkout has newer source changes', async () => {
    const { root, targetSha } = fixture();
    write(root, 'pulumi/index.ts', 'new source\n');
    commit(root);
    expect((await checkOpenWiki(root, targetSha)).fresh).toBe(true);
});

for (const value of [
    null,
    [],
    {},
    { status: 'complete' },
    { status: 'complete', gitHead: 'HEAD' },
    { status: 'complete', gitHead: 'f'.repeat(40) },
    { status: 'interrupted', gitHead: 'f'.repeat(40) },
]) {
    test(`rejects unusable checkpoint ${JSON.stringify(value)}`, async () => {
        const { root, targetSha } = fixture();
        checkpoint(root, value);
        await expect(checkOpenWiki(root, targetSha)).rejects.toThrow();
    });
}

test('rejects interrupted generation even at a valid source SHA', async () => {
    const { root, sourceSha, targetSha } = fixture();
    checkpoint(root, { status: 'interrupted', gitHead: sourceSha });
    await expect(checkOpenWiki(root, targetSha)).rejects.toThrow(/status/);
});

test('rejects a checkpoint outside the target ancestry', async () => {
    const { root, sourceSha, targetSha } = fixture();
    git(root, 'checkout', '-q', '--detach', sourceSha);
    write(root, 'other.md', 'different history\n');
    const otherSha = commit(root);
    git(root, 'checkout', '-q', '--detach', targetSha);
    checkpoint(root, { status: 'complete', gitHead: otherSha });
    await expect(checkOpenWiki(root, targetSha)).rejects.toThrow(/ancestor/);
});

test('rejects a SHA pointing to a blob instead of a commit', async () => {
    const { root, targetSha } = fixture();
    checkpoint(root, {
        status: 'complete',
        gitHead: git(root, 'rev-parse', 'HEAD:pulumi/index.ts'),
    });
    await expect(checkOpenWiki(root, targetSha)).rejects.toThrow();
});

for (const path of ['openwiki', 'openwiki/.last-update.json', 'openwiki/quickstart.md']) {
    test(`rejects missing ${path}`, async () => {
        const { root, targetSha } = fixture();
        rmSync(join(root, path), { recursive: true });
        await expect(checkOpenWiki(root, targetSha)).rejects.toThrow();
    });
}

test('rejects malformed JSON', async () => {
    const { root, targetSha } = fixture();
    write(root, 'openwiki/.last-update.json', '{');
    await expect(checkOpenWiki(root, targetSha)).rejects.toThrow();
});

for (const target of ['', 'HEAD', '--help', 'f'.repeat(40)]) {
    test(`rejects invalid or nonexistent target ${JSON.stringify(target)}`, async () => {
        const { root } = fixture();
        await expect(checkOpenWiki(root, target)).rejects.toThrow();
    });
}

test('CLI prints source, explicit target and observation timestamp, exits 0 only when fresh', () => {
    const { root, sourceSha, targetSha } = fixture();
    const fresh = cli(root, '--against', targetSha);
    expect(fresh.exitCode).toBe(0);
    expect(fresh.stdout.toString()).toContain(sourceSha);
    expect(fresh.stdout.toString()).toContain(targetSha);
    expect(fresh.stdout.toString()).toContain('2026-10-24T00:00:00.000Z');
    write(root, 'pulumi/index.ts', 'changed\n');
    const newerSha = commit(root);
    const stale = cli(root, '--against', newerSha);
    expect(stale.exitCode).toBe(1);
    expect(stale.stdout.toString()).toContain('pulumi/index.ts');
    expect(stale.stdout.toString()).toContain(newerSha);
});

test('CLI reports checkpoint failures and rejects a missing or extra argument', () => {
    const { root, targetSha } = fixture();
    rmSync(join(root, 'openwiki'), { recursive: true });
    const missing = cli(root, '--against', targetSha);
    expect(missing.exitCode).toBe(1);
    expect(missing.stderr.toString()).toContain('openwiki');
    for (const args of [[], ['--against'], ['--against', targetSha, 'extra']]) {
        const result = cli(root, ...args);
        expect(result.exitCode).toBe(1);
        expect(result.stderr.toString()).toContain('--against');
    }
});

const excluded = [
    'docs/superpowers/specs/plan.md', 'README.md', 'RUNBOOK.md',
    'pulumi/sdks/selectel/index.ts', 'pulumi/node_modules/tracked.txt',
    'pulumi/.pulumi/stacks/prod.json', 'pulumi/state.json', 'pulumi/plan.json',
    'pulumi/Pulumi.prod.yaml', 'pulumi/bootstrap/Pulumi.main.yaml',
    'pulumi/bootstrap/env.sh', 'ansible/env.sh', 'ansible/clouds.yaml',
    'ansible/clouds.yaml.example', 'ansible/inventory/group_vars/all/vault.yml',
    'ansible/inventory/group_vars/all/vars.yml', 'ansible/.vault-password',
    'ansible/files/authorized_keys/user.pub', 'ansible/secrets/key.pem',
    'ansible/.env', 'ansible/.env.local', 'pulumi/dist/index.js',
    'pulumi/selectel.env', 'ansible/id_rsa', 'ansible/id_ed25519',
    'pulumi/docs/superpowers/old.md', 'pulumi/state.json.bak',
    'openwiki/architecture.md', '.github/workflows/automation.yml',
];
for (const path of excluded) {
    test(`excluded change in ${path} keeps the wiki fresh`, async () => {
        const { root } = fixture();
        write(root, path, 'changed\n');
        expect((await checkOpenWiki(root, commit(root))).changedPaths).toEqual([]);
    });
}

for (const path of [
    'AGENTS.md',
    'openwiki/INSTRUCTIONS.md',
    'ansible/roles/app/templates/compose.yml.j2',
    'outside\nfile.ts',
]) {
    test(`wiki PR gate rejects mixed changes: ${path}`, () => {
        const { root, targetSha } = fixture();
        write(root, path, 'changed\n');
        write(root, 'openwiki/new.md', 'generated\n');
        const head = commit(root);
        const result = Bun.spawnSync(
            [process.execPath, resolve(import.meta.dir, 'openwiki-check-pr.ts'), targetSha, head],
            { cwd: root },
        );
        expect(result.exitCode).toBe(1);
        expect(result.stderr.toString()).toContain('source paths');
    });
}
test('wiki PR gate accepts generated pages and rejects abbreviated SHAs', () => {
    const { root, sourceSha, targetSha } = fixture();
    const run = (base: string) =>
        Bun.spawnSync(
            [process.execPath, resolve(import.meta.dir, 'openwiki-check-pr.ts'), base, targetSha],
            { cwd: root },
        );
    expect(run(sourceSha).exitCode).toBe(0);
    expect(run(sourceSha.slice(0, 12)).exitCode).toBe(1);
});
test('workflow selects generated wiki changes, excluding the source brief', () => {
    const workflow = Bun.YAML.parse(
        readFileSync(
            resolve(import.meta.dir, '../.github/workflows/openwiki-freshness.yml'),
            'utf8',
        ),
    ) as { on: { pull_request: { paths: string[] } } };
    expect(workflow.on.pull_request.paths).toEqual(['openwiki/**', '!openwiki/INSTRUCTIONS.md']);
});

test('closed generator corpus agrees with freshness for allowed and excluded fixtures', () => {
    const { root } = fixture();
    write(root, '.gitignore', readFileSync(resolve(import.meta.dir, '../.openwikiignore'), 'utf8'));
    for (const path of [...sources, ...excluded]) {
        const result = Bun.spawnSync(['git', 'check-ignore', '--no-index', '-q', path], { cwd: root });
        expect(result.exitCode, path).toBe(excluded.includes(path) && !path.startsWith('openwiki/') ? 0 : 1);
    }
    expect(Bun.spawnSync(['git', 'check-ignore', '--no-index', '-q', 'unlisted/new.txt'], { cwd: root }).exitCode).toBe(0);
});
test('wiki CI has no production environment or secrets; infra CI excludes wiki paths', () => {
    const text = readFileSync(resolve(import.meta.dir, '../.github/workflows/openwiki-freshness.yml'), 'utf8');
    expect(text).not.toMatch(/environment:|secrets\.|pulumi (up|preview)|ansible-playbook/);
    const infra = Bun.YAML.parse(readFileSync(resolve(import.meta.dir, '../.github/workflows/infra.yml'), 'utf8')) as any;
    expect(infra.on.pull_request.paths).toEqual(['pulumi/**', 'ansible/**', '.github/workflows/infra.yml']);
    expect(infra.on.push.paths).toEqual(infra.on.pull_request.paths);
});
