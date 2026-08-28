from __future__ import annotations
import unittest
from tools.oracle.build_provenance import ProvenanceError,validate_manifest

def d(ch='1'):return 'sha256:'+ch*64
def policy():return {'schema':'trillionnium.oracle-instrumentation-policy.v1','project_id':'trillionnium-game','status':'candidate-review-required','upstream':{'repository':'heroiclabs/nakama','commit':'d4d92f93f78bbbe62c7fc50a3f85c772ec121a09','tree':'f3c9cfc2726d5543da1564629170f35b98e3797d'},'allowed_capabilities':['clock_capture','random_capture','provider_intent_capture','database_effect_capture','runtime_hook_capture','trace_correlation'],'allowed_added_prefixes':['internal/trnm_oracle/','server/trnm_oracle_'],'allowed_modified_paths':[],'forbidden_prefixes':['migrate/','data/','console/acl/','iap/','social/','se/'],'forbidden_exact_paths':['server/api_authenticate.go'],'policy':{'deletions_allowed':False,'semantic_behavior_change_allowed':False,'networked_build_allowed':False,'floating_image_or_dependency_allowed':False,'self_approval_allowed':False,'positive_equivalence_claim_allowed':False}}
def manifest():return {'schema':'trillionnium.instrumented-oracle-build.v1','project_id':'trillionnium-game','status':'candidate-unreviewed','upstream':policy()['upstream'],'patch':{'sha256':d('1'),'files':[{'path':'internal/trnm_oracle/clock.go','change_type':'added','old_blob':None,'new_blob':'1'*40,'diff_sha256':d('2'),'capabilities':['clock_capture'],'semantics_impact':'none-claimed','review_status':'unreviewed'}]},'toolchain':{'go_version':'go1.26.5','docker_version':'28.0.0','buildkit_version':'0.20.0','platform':'linux/amd64','go_binary_sha256':d('3'),'dockerfile_sha256':d('4'),'base_image_digest':d('5')},'build':{'command':['docker','buildx','build','--network=none'],'network_mode':'none','context_sha256':d('6'),'source_date_epoch':1770000000},'image':{'image_id':d('7'),'oci_digest':d('8'),'sbom_sha256':d('9'),'provenance_sha256':d('a')},'review':{'reviewers':[],'self_approval':False,'review_evidence_sha256':None},'claims':{'instrumented_equivalence':False,'sg2_complete':False,'compatibility_credit':False,'production_ready':False,'public_online':False}}
class Tests(unittest.TestCase):
 def test_valid_candidate_is_not_approved(self):
  r=validate_manifest(manifest(),policy());self.assertEqual(r['status'],'candidate-valid');self.assertFalse(r['approved']);self.assertFalse(r['sg2_complete'])
 def test_unallowlisted_modification_is_rejected(self):
  m=manifest();m['patch']['files'][0].update({'path':'server/session.go','change_type':'modified','old_blob':'2'*40})
  with self.assertRaises(ProvenanceError):validate_manifest(m,policy())
 def test_forbidden_source_and_deletion_are_rejected(self):
  for path,change in [('iap/iap.go','modified'),('internal/trnm_oracle/x.go','deleted')]:
   m=manifest();m['patch']['files'][0].update({'path':path,'change_type':change,'old_blob':'2'*40})
   with self.assertRaises(ProvenanceError):validate_manifest(m,policy())
 def test_network_or_floating_build_is_rejected(self):
  for command in [['curl','https://example.com/x'],['docker','build','base:latest']]:
   m=manifest();m['build']['command']=command
   with self.assertRaises(ProvenanceError):validate_manifest(m,policy())
 def test_positive_claim_is_rejected(self):
  m=manifest();m['claims']['instrumented_equivalence']=True
  with self.assertRaises(ProvenanceError):validate_manifest(m,policy())
 def test_approved_requires_two_reviewers(self):
  m=manifest();m['status']='reviewed-approved';m['patch']['files'][0]['review_status']='approved';m['review']={'reviewers':['alice','bob'],'self_approval':False,'review_evidence_sha256':d('b')};r=validate_manifest(m,policy(),require_approved=True);self.assertTrue(r['approved']);self.assertFalse(r['instrumented_equivalence'])
if __name__=='__main__':unittest.main()
