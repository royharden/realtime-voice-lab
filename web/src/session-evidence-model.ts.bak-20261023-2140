// A closed display projection of saved evidence, not a relay payload validator.
// Unknown/custom producer semantics remain unknown; no totals or latency are derived.
import {isHash,safeRelativePath} from './review-model';
import {LITERALS} from './generated/contracts';
export type EvidenceOwner={session_id:string;run_id:string;boot_id:string;trial_id:string};
type Rule='id'|'hash'|'path'|'integer'|'decimal'|'boolean'|'flag'|readonly string[];
type Fields=Record<string,Rule>;
type Row=Record<string,unknown>;
const ID=/^[A-Za-z0-9_-]{1,96}$/;
export const evidenceIdentifier=(v:unknown):v is string=>typeof v==='string'&&ID.test(v);
const obj=(v:unknown):Row=>v!==null&&typeof v==='object'&&!Array.isArray(v)?v as Row:{};
const OUTCOMES=LITERALS.TerminalOutcome;
const identity:Fields={session_id:'id',run_id:'id',trial_id:'id',boot_id:'id',connection_epoch:'integer',response_epoch:'integer'};
const terminal:Fields={...identity,arm:['V','C','K'],mode:['mock','live'],terminal_outcome:OUTCOMES,terminal_source:['server','client','relay','browser','deadline','transport','unknown','restart_reconciliation','server_lifecycle','client_status','client_report','mock_fault_simulation'],trace_hash:'hash',trace_complete:'flag',run_manifest_hash:'hash',started_ns:'integer'};
const artifact:Fields={content_hash:'hash',artifact_hash:'hash',artifact_reference:'path',bytes:'integer',scope:['run_ledger_snapshot'],captured_for_session_id:'id',run_id:'id',boot_id:'id',server_timestamp_ns:'integer',clock_domain:['server_monotonic_ns'],kind:['trace','relay_trace','ledger_snapshot','fault_controls','calibration_phases','session_journal','run_manifest','licensed_control'],path:'path',reference:'path',size_bytes:'integer',observation:['unknown','observed','saved_snapshot','recorded'],reason:['terminal_snapshot_unavailable','saved_snapshot_missing_or_hash_invalid','ledger_snapshot_write_or_read_failure','calibration_snapshot_unavailable','fault_receipts_snapshot_unavailable']};
const attempt:Fields={...identity,attempt_id:'id',logical_turn_id:'id',transport_turn_id:'id',provider_request_id:'id',component:['stt','tts','native','canned'],arm:['V','C','K'],reservation:'decimal',settled:'decimal',price_version:'id',currency:['USD'],state:['reserved','settled','unknown','released','dispatched','failed'],dispatch_authorized:'boolean',dispatched:'boolean',started:'boolean',owner_id:'id',lineage_observation:['recorded_at_reservation','unknown']};
const phase:Fields={...identity,phase_id:'id',status:['active','ended'],started_ns:'integer',deadline_ns:'integer',ended_ns:'integer',session_deadline_ns:'integer',absolute_cap_ms:'integer',phase_limit:'integer',clock_domain:['server_ns','server_monotonic_ns'],duplicate:'boolean',task_deadlines_retained:'boolean',captured_turn_created:'boolean',probe_success:['unknown'],runtime_verified:'boolean'};
function value(v:unknown,r:Rule):unknown{
  if(v===null)return null;
  if(Array.isArray(r))return typeof v==='string'&&r.includes(v)?v:null;
  if(r==='id')return evidenceIdentifier(v)?v:null;
  if(r==='hash')return isHash(v)?v:null;
  if(r==='path')return typeof v==='string'&&v.length<=256&&safeRelativePath(v)?v:null;
  if(r==='integer')return typeof v==='number'&&Number.isSafeInteger(v)&&v>=0?v:null;
  if(r==='decimal')return typeof v==='string'&&/^\d{1,24}(?:\.\d{1,24})?$/.test(v)?v:null;
  if(r==='flag')return typeof v==='boolean'||v===0||v===1?v:null; // Preserve SQLite flag representation exactly.
  return typeof v==='boolean'?v:null;
}
function pick(v:unknown,fields:Fields):Row{
  const source=obj(v),out:Row={};
  for(const [key,rule] of Object.entries(fields))out[key]=value(source[key],rule);
  return out;
}
function rows(v:unknown,fields:Fields,filter?:(r:Row)=>boolean,extra?:(r:Row)=>Row){
  if(!Array.isArray(v))return {observation:'unknown',received_rows:null,shown_rows:0,truncated:false,rows:[]};
  const selected=v.slice(0,256).map((r,index)=>({source_index:index,...(filter&&!filter(obj(r))?{observation:'unresolved_or_foreign_row',attribution:'unknown'}:{...pick(r,fields),...extra?.(obj(r))})}));
  return {observation:'received_metadata',received_rows:v.length,shown_rows:selected.length,truncated:v.length>256,rows:selected};
}
// JSON input is byte bounded before parsing. Reject excessive nesting/nodes before projection.
function boundedTree(root:unknown){
  const pending:[unknown,number][]=[[root,0]];let count=0;
  while(pending.length){const [node,depth]=pending.pop()!;if(++count>40000||depth>12)throw new Error('metadata_structure_bound');
    if(node&&typeof node==='object')for(const v of Object.values(node)){if(pending.length>=40000)throw new Error('metadata_structure_bound');pending.push([v,depth+1]);}}
}
export function projectSessionEvidence(input:unknown,owner:EvidenceOwner){
  boundedTree(input);const root=obj(input),session=obj(root.session);
  if(root.schema_version!==1)throw new Error('evidence_schema_version');
  for(const key of ['session_id','run_id','boot_id','trial_id'] as const){
    if(root[key]!==owner[key]||session[key]!==owner[key])throw new Error('evidence_identity_mismatch');
  }
  const task=obj(root.task_journal),taskState=obj(task.task),lineage=obj(root.attempt_lineage);
  const ledgerFields:Fields={budget:'decimal',known_cost:'decimal',unknown_count:'integer',unknown_exposure:'decimal',complete:'boolean',total_exposure:'decimal',currency:['USD'],bound_violation_count:'integer',bound_integrity:['invalid','not_disproved'],bound_violation_attempt_count:'integer',dispatch_halt_active:'boolean',dispatch_allowed:'boolean',owner_id:'id',snapshot_owner_id:'id'};
  const operationFields:Fields={id:'integer',namespace:'id',operation_id:'id',turn_id:'id',epoch:'integer',duplicate:'integer',command:['get_hours','list_slots','select_slot','confirm_slot','get_status']};
  const taskExtra=(r:Row)=>({outcome:pick(r.outcome,{ok:'boolean',code:['logical_operation_conflict','stale_epoch','select_offered_slot','explicit_current_confirmation_required','mock_drop_before_effect','already_booked'],logical_operation_id:'id',state_version:'integer',effect_committed:'boolean',retryable_no_effect:'boolean',state:['information','slots_offered','awaiting_confirmation','booked','complete'],slot_id:'id'})});
  const taskMatches=task.trial_namespace===owner.trial_id;
  const licensed=obj(obj(root.admission).licensed_control);
  const scopedControl=licensed.kind==='licensed_fixed_pcm'&&licensed.provider_cost_scope==='no_provider_component_only'&&licensed.provider_generation_cost_usd==='0';
  return {
    observation:'received_saved_metadata',runtime_verification:'unknown',product_acceptance:false,
    identity:pick(root,identity),session:pick(session,terminal),
    epoch_note:'HTTP session epochs absent remain null; browser envelope epochs are shown separately, never inferred from turns.',
    terminal_note:'control_complete describes control execution only. Spoken/task/confirmation correctness remains unknown; K task applicability is not_applicable or unknown.',
    turns:rows(root.turns,{...identity,transport_turn_id:'id',logical_turn_id:'id',started_ns:'integer',terminal_outcome:OUTCOMES}),
    saved_artifacts:rows(root.artifacts,artifact),trace_reference:value(root.trace_reference,'path'),
    ledger_artifact:pick(root.ledger_artifact,artifact),calibration_artifact:pick(root.calibration_artifact,artifact),faults_artifact:pick(root.faults_artifact,artifact),
    artifact_note:'Hash/path references received from producer; bytes not independently verified, never dereferenced.',
    run_snapshot:{scope:'whole_run_saved_snapshot_not_session_total',...pick(root.ledger,ledgerFields),aggregation:'none; refresh replaces this snapshot'},
    session_attempts:rows(root.session_attempts,attempt,r=>r.session_id===owner.session_id&&r.lineage_observation==='recorded_at_reservation'),
    attempt_lineage:{...pick(lineage,{scope:['exact_session_id_from_saved_run_snapshot_only'],observation:['saved_snapshot','unknown'],other_session_attempts_excluded:'boolean',inferred_joins:'boolean'}),unresolved_attempt_ids:rows(Array.isArray(lineage.unresolved_attempt_ids)?lineage.unresolved_attempt_ids.map(attempt_id=>({attempt_id})):undefined,{attempt_id:'id'})},
    task_journal:taskMatches?{observation:'received_metadata',correctness:'unknown',state:pick(taskState,{state:['information','slots_offered','awaiting_confirmation','booked','complete'],state_version:'integer',response_epoch:'integer',slot_id:'id'}),booking:pick(taskState.booking,{namespace:'id',operation_id:'id',slot_id:'id',confirmation_turn:'id',epoch:'integer',state_version:'integer'}),attempt_count:value(task.attempt_count,'integer'),truncated:value(task.truncated,'boolean'),original_operations:rows(task.original_operations,operationFields,r=>r.namespace===owner.trial_id,taskExtra),attempts:rows(task.attempts,operationFields,r=>r.namespace===owner.trial_id,taskExtra)}:{observation:'unknown_or_trial_mismatch',correctness:'unknown'},
    licensed_control:scopedControl?{...pick(licensed,{kind:['licensed_fixed_pcm'],config_hash:'hash',catalog_hash:'hash',authorization_reference:'id',capability_reference:'id',approval_observation:['configuration_only_not_verified','external_verifier_accepted'],asset_observation:['local_wav_and_pcm_hashes_verified_at_open'],provider_generation_cost_usd:['0'],provider_cost_scope:['no_provider_component_only'],other_costs:['unknown'],task_applicability:['not_applicable'],spoken_correctness:['unknown'],independent_verification:['unknown']}),asset_hashes:rows(Object.entries(obj(licensed.asset_hashes)).map(([asset_id,hash])=>({asset_id,hash})),{asset_id:'id',hash:'hash'}),acceptance:'unknown; producer declaration only'}:{observation:'unknown',provider_generation_cost_usd:null,other_costs:'unknown',task_applicability:'unknown'},
    calibration_phases:rows(root.calibration_phases,phase),
    boundaries:{clock_domain:value(root.clock_domain,['server_monotonic_ns']),clock_scope:value(root.clock_scope,['boot_id']),latency:'unknown; no cross-clock subtraction',physical_sound:'unknown',spoken_correctness:'unknown',task_correctness:'unknown',confirmation_correctness:'unknown',provider_generation_zero:'Only applicable when the producer explicitly declares no-provider scope; not inferred here.',other_costs:'unknown'},
    projection:{unknown_or_missing_fields:null,max_rows_per_list:256,max_depth:12,max_nodes:40000,opaque_fields:'excluded; this is a display projection, not schema validation or acceptance'}
  };
}
