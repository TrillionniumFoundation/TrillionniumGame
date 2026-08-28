from __future__ import annotations
import importlib.util,json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];SCRIPT=ROOT/'scripts/generate-database-denominator.py'
SPEC=importlib.util.spec_from_file_location('dbden',SCRIPT);assert SPEC and SPEC.loader
mod=importlib.util.module_from_spec(SPEC);sys.modules[SPEC.name]=mod;SPEC.loader.exec_module(mod)
from tools.upstream.pinned_archive import LOCK_FILE,git_tree_sha1
SQL1="""-- +migrate Up
CREATE TABLE IF NOT EXISTS users (
 PRIMARY KEY (id),
 id UUID NOT NULL,
 username TEXT NOT NULL UNIQUE,
 metadata JSONB NOT NULL DEFAULT '{}',
 CHECK (length(username) > 0)
);
INSERT INTO users (id, username) VALUES ('0', 'semi;colon') ON CONFLICT DO NOTHING;
CREATE UNIQUE INDEX IF NOT EXISTS users_name_idx ON users (username);
-- +migrate Down
DROP TABLE IF EXISTS users, groups;
"""
SQL2="""-- +migrate Up
ALTER TABLE users ADD COLUMN email TEXT, ALTER COLUMN username SET NOT NULL;
UPDATE users SET metadata = '{}' WHERE metadata IS NULL;
DO $$ BEGIN PERFORM ';'; END $$;
-- +migrate Down
ALTER TABLE users DROP COLUMN email;
"""
def locked(root:Path,repo:str,rev:str)->str:
    d=root/'migrate/sql';d.mkdir(parents=True)
    (d/'001_initial.sql').write_text(SQL1);(d/'002_update.sql').write_text(SQL2)
    tree=git_tree_sha1(root);(root/LOCK_FILE).write_text(json.dumps({'repository':repo,'revision':rev,'tree':tree,'verification':'recomputed-git-tree-sha1'}));return tree
class Tests(unittest.TestCase):
    def test_split_handles_strings_comments_and_dollar_quotes(self):
        sections=mod.split_sections(SQL2);up=next(s for s in sections if s[0]=='up');sts=mod.split_statements(up[1],up[0],up[2])
        self.assertEqual(len(sts),3);self.assertIn("';'",sts[2].text)
    def test_classifies_table_columns_constraints_index_and_backfill(self):
        sections=mod.split_sections(SQL1);up=next(s for s in sections if s[0]=='up');all_items=[]
        for st in mod.split_statements(up[1],up[0],up[2]):all_items.extend(mod.classify_statement(st,'migrate/sql/001_initial.sql')[0])
        classes={x['class'] for x in all_items}
        self.assertTrue({'db_table','db_column','db_constraint','db_inline_constraint','db_index','data_backfill','data_invariant_candidate','data_default_candidate'}<=classes)
        defaults=[x for x in all_items if x['class']=='data_default_candidate']
        self.assertEqual([x['symbol'] for x in defaults], ['users.metadata.default'])
        invariants=[x['symbol'] for x in all_items if x['class']=='data_invariant_candidate']
        self.assertNotIn('users.metadata.default', invariants)
        down=next(s for s in sections if s[0]=='down')
        drops=[]
        for st in mod.split_statements(down[1],down[0],down[2]):drops.extend(mod.classify_statement(st,'migrate/sql/001_initial.sql')[0])
        self.assertEqual({x['symbol'] for x in drops if x['class']=='db_drop_table'}, {'users','groups'})
    def test_unknown_statement_is_manual(self):
        from tools.denominator.sql_migration_surface import Statement
        st=Statement('MERGE INTO users USING x ON true WHEN MATCHED THEN DO NOTHING','up',1,1,1)
        _,manual=mod.classify_statement(st,'x.sql');self.assertEqual(manual[0]['class'],'unparsed_sql_statement')
    def test_full_candidate_deterministic_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            b=Path(td);r=b/'source';r.mkdir();rev='1'*40;tree=locked(r,'test/nakama',rev);orig=(mod.REPOSITORY,mod.COMMIT,mod.TREE);mod.REPOSITORY,mod.COMMIT,mod.TREE='test/nakama',rev,tree
            try:
                a=b/'a';c=b/'c';x=mod.generate(r,a);y=mod.generate(r,c);self.assertEqual(x,y)
                for n in ('database-denominator.candidate.json','data-denominator.candidate.json','database-data-reconciliation.candidate.json'):self.assertEqual((a/n).read_bytes(),(c/n).read_bytes())
                db=json.loads((a/'database-denominator.candidate.json').read_text());data=json.loads((a/'data-denominator.candidate.json').read_text())
                self.assertEqual(db['unclassified_count'],db['leaf_count']);self.assertFalse(any(db['claims'].values()));self.assertGreater(data['leaf_count'],0)
                with self.assertRaises(mod.DenominatorError):mod.require_sg1(a)
            finally:mod.REPOSITORY,mod.COMMIT,mod.TREE=orig
    def test_post_fetch_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            b=Path(td);r=b/'source';r.mkdir();rev='1'*40;tree=locked(r,'test/nakama',rev);(r/'migrate/sql/001_initial.sql').write_text(SQL1+'--tamper\n');orig=(mod.REPOSITORY,mod.COMMIT,mod.TREE);mod.REPOSITORY,mod.COMMIT,mod.TREE='test/nakama',rev,tree
            try:
                with self.assertRaises(mod.DenominatorError):mod.generate(r,b/'out')
            finally:mod.REPOSITORY,mod.COMMIT,mod.TREE=orig
if __name__=='__main__':unittest.main()
