import { expect, test } from 'bun:test';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const path = resolve(import.meta.dir, '../.github/workflows/publish-docs-wiki.yml');
const workflow = Bun.YAML.parse(existsSync(path) ? readFileSync(path, 'utf8') : '{}') as {
    jobs: { publish: { steps: { run: string }[] } };
};

test('only dispatches docs Pages for main wiki pushes or manual diagnosis', () => {
    expect(workflow as unknown).toEqual({
        name: 'Publish docs wiki',
        on: { push: { branches: ['main'], paths: ['openwiki/**'] }, workflow_dispatch: null },
        permissions: {},
        jobs: {
            publish: {
                'runs-on': 'ubuntu-latest',
                'timeout-minutes': 5,
                steps: [
                    {
                        name: 'Dispatch docs Pages',
                        env: { GH_TOKEN: '${{ secrets.DOCS_PAGES_TOKEN }}' },
                        run: expect.any(String),
                    },
                ],
            },
        },
    });
});

for (const [token, ghExit, expectedExit] of [
    ['', 0, 1],
    ['local-test-credential', 0, 0],
    ['local-test-credential', 17, 17],
] as const) {
    test(`dispatch with ${token ? 'a credential' : 'no credential'} and gh exit ${ghExit}`, () => {
        const root = mkdtempSync(join(tmpdir(), 'wiki-dispatch-'));
        try {
            const argsFile = join(root, 'args');
            writeFileSync(
                join(root, 'gh'),
                `#!/bin/bash
printf '%s\\n' "$@" > "$ARGS_FILE"
[[ "$GH_TOKEN" == 'local-test-credential' ]] || exit 98
exit "$GH_EXIT"
`,
                { mode: 0o700 },
            );
            const result = Bun.spawnSync(
                [
                    'bash',
                    '--noprofile',
                    '--norc',
                    '-e',
                    '-o',
                    'pipefail',
                    '-c',
                    workflow.jobs.publish.steps[0]!.run,
                ],
                {
                    env: {
                        PATH: `${root}:/usr/bin:/bin`,
                        GH_TOKEN: token,
                        GH_EXIT: String(ghExit),
                        ARGS_FILE: argsFile,
                    },
                },
            );
            expect(result.exitCode).toBe(expectedExit);
            const output = result.stdout.toString() + result.stderr.toString();
            expect(output).not.toContain('local-test-credential');
            if (token) {
                expect(readFileSync(argsFile, 'utf8').trim().split('\n')).toEqual([
                    'workflow',
                    'run',
                    'pages.yml',
                    '--ref',
                    'main',
                    '--repo',
                    'Cringe-Driven-Development-Team/docs',
                ]);
            } else {
                expect(output).toContain('DOCS_PAGES_TOKEN');
                expect(existsSync(argsFile)).toBe(false);
            }
        } finally {
            rmSync(root, { recursive: true, force: true });
        }
    });
}
