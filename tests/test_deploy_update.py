"""Real divergent Git checkouts with simulated Docker; never touches live services."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'deploy/update-vps.sh'


class DeployUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='captureit-deploy-test-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.upstream=self.root/'upstream';self.source=self.root/'vps';self.upstream.mkdir()
        self.git(self.upstream,'init','-b','main');(self.upstream/'deploy').mkdir()
        (self.upstream/'deploy/update-vps.sh').write_text(SCRIPT.read_text())
        (self.upstream/'deploy/backup-vps.sh').write_text("#!/bin/bash\necho '[\"backup\"]' >> \"$QA_DOCKER_LOG\"\nexit \"${QA_BACKUP_FAIL:-0}\"\n")
        (self.upstream/'docker-compose.yml').write_text('services: {}\n')
        self.git(self.upstream,'add','.');self.git(self.upstream,'commit','-m','base')
        self.git(self.root,'clone',str(self.upstream),str(self.source))
        (self.source/'local-only.txt').write_text('local commit\n');self.git(self.source,'add','.');self.git(self.source,'commit','-m','VPS local change')
        self.local_head=self.git(self.source,'rev-parse','HEAD').strip()
        (self.source/'local-only.txt').write_text('preserve uncommitted change\n');(self.source/'.env').write_text('QA_SETTING=preserved\n')
        (self.upstream/'new-release.txt').write_text('new code\n');self.git(self.upstream,'add','.');self.git(self.upstream,'commit','-m','remote release')
        self.remote_head=self.git(self.upstream,'rev-parse','HEAD').strip()
        binary=self.root/'bin';binary.mkdir();(binary/'id').write_text('#!/bin/sh\necho 0\n');(binary/'id').chmod(0o755)
        (binary/'docker').write_text('''#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
with open(os.environ['QA_DOCKER_LOG'],'a') as out:out.write(json.dumps(args)+'\\n')
if args[0]=='ps':print('existing-container')
elif args[0]=='inspect':
    if 'Running' in args[2]:print('true')
    else:print(json.dumps([{'Destination':'/data','Type':'volume','Name':os.environ.get('QA_VOLUME','captureit-operational_ops-data')}]))
elif 'build' in args:sys.exit(int(os.environ.get('QA_BUILD_FAIL','0')))
''');(binary/'docker').chmod(0o755)
        self.log=self.root/'docker.log'
        self.env={**os.environ,'PATH':str(binary)+os.pathsep+os.environ['PATH'],'OPS_SOURCE_ROOT':str(self.source),
                  'OPS_RELEASE_ROOT':str(self.root/'releases'),'OPS_COMPOSE_PROJECT':'captureit-operational','QA_DOCKER_LOG':str(self.log)}

    def git(self,folder,*args):
        return subprocess.check_output(['git','-c','user.name=QA','-c','user.email=qa@example.test','-C',str(folder),*args],stderr=subprocess.DEVNULL,text=True)

    def run_update(self,**extra):
        result=subprocess.run(['bash',str(SCRIPT)],cwd=self.source,env={**self.env,**extra},capture_output=True,text=True,timeout=15)
        calls=[json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        self.assertEqual(self.git(self.source,'rev-parse','HEAD').strip(),self.local_head)
        self.assertEqual((self.source/'local-only.txt').read_text(),'preserve uncommitted change\n')
        self.assertFalse(any('down' in c or '--volumes' in c for c in calls));return result,calls

    def test_divergent_checkout_deploys_remote_without_reset(self):
        result,calls=self.run_update();self.assertEqual(result.returncode,0,result.stderr)
        release=next((self.root/'releases').iterdir());self.assertEqual(self.git(release,'rev-parse','HEAD').strip(),self.remote_head)
        self.assertEqual((release/'.env').read_text(),'QA_SETTING=preserved\n');self.assertEqual((release/'.env').stat().st_mode&0o777,0o600)
        build=next(i for i,c in enumerate(calls) if 'build' in c);backup=calls.index(['backup']);start=next(i for i,c in enumerate(calls) if 'up' in c)
        self.assertLess(build,backup);self.assertLess(backup,start)
        self.assertTrue(all(c[c.index('-p')+1]=='captureit-operational' for c in calls if '-p' in c))
        self.assertEqual(next(c for c in calls if 'exec' in c)[-1],self.remote_head)

    def test_failed_fetch_never_calls_docker(self):
        self.git(self.source,'remote','set-url','origin',str(self.root/'missing'));result,calls=self.run_update()
        self.assertNotEqual(result.returncode,0);self.assertEqual(calls,[])

    def test_wrong_data_volume_stops_before_build(self):
        result,calls=self.run_update(QA_VOLUME='other-data');self.assertNotEqual(result.returncode,0)
        self.assertFalse(any('build' in c or 'up' in c for c in calls))

    def test_build_or_backup_failure_never_recreates_service(self):
        for failure in ('QA_BUILD_FAIL','QA_BACKUP_FAIL'):
            with self.subTest(failure=failure):
                self.env['OPS_RELEASE_ROOT']=str(self.root/failure);self.log.unlink(missing_ok=True)
                result,calls=self.run_update(**{failure:'1'});self.assertNotEqual(result.returncode,0)
                self.assertFalse(any('up' in c for c in calls))
                if failure=='QA_BUILD_FAIL':self.assertNotIn(['backup'],calls)
