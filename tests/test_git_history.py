from pathlib import Path
import subprocess
import tempfile
import unittest
from tools.git_history import GitHistory


class GitHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.article=self.root/'posts';self.article.mkdir()
        self.git(self.root,'init');self.git(self.root,'config','user.name','Fixture');self.git(self.root,'config','user.email','fixture@example.invalid')
        for path,value in [(self.article/'a.granka.json','{}'),(self.article/'a.md','Before'),(self.root/'other.md','Other')]:path.write_text(value,encoding='utf-8')
        self.git(self.root,'add','posts/a.granka.json','posts/a.md','other.md');self.git(self.root,'commit','-m','fixture')
        self.history=GitHistory(lambda:{'blog':self.article},lambda root,rel:self.article/rel,self.git)

    def tearDown(self):self.temp.cleanup()

    @staticmethod
    def git(root,*args,timeout=60):
        return subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,encoding='utf-8',timeout=timeout)

    def test_parent_repository_and_foreign_staging_preserved(self):
        (self.article/'a.md').write_text('After',encoding='utf-8');(self.root/'other.md').write_text('Unfinished',encoding='utf-8')
        self.git(self.root,'add','other.md')
        self.assertTrue(self.history.state('blog','a.granka.json')['repo'])
        result=self.history.sync('blog','article snapshot',push=False,relative='a.granka.json')
        self.assertTrue(result['committed'])
        self.assertEqual(self.git(self.root,'diff','--cached','--name-only').stdout.strip(),'other.md')
        self.assertEqual(self.git(self.root,'show','--pretty=','--name-only','HEAD').stdout.strip(),'posts/a.md')

    def test_failed_push_is_retried_with_clean_worktree(self):
        calls=[];normal=self.history.run
        def run(root,*args,**kwargs):
            if args[0]=='push':calls.append(args);return subprocess.CompletedProcess(args,1 if len(calls)==1 else 0,'','offline')
            return normal(root,*args,**kwargs)
        self.history.run=run
        first=self.history.sync('blog','snapshot',relative='a.granka.json');second=self.history.sync('blog','retry',relative='a.granka.json')
        self.assertFalse(first['pushed']);self.assertTrue(second['pushed']);self.assertEqual(len(calls),2)
