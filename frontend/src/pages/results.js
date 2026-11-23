import {plot} from '../components/common/chart.js';
import {feedback} from '../components/common/fields.js';
import {empty, metrics, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {REVIEW, STATUS} from '../constants/enums.js';
import {handler, poll} from '../hooks/action.js';
import {workspace} from '../stores/workspace.js';
import {button, el, h, link, p} from '../utils/dom.js';
import {date, value} from '../utils/format.js';

const INTERFERENCE_STATE = {stable:'抗干扰稳定', vulnerable:'存在干扰反例', no_peak:'原扫描无峰'};

function peakText(peak) {
  if (!peak) return '无可判读峰';
  return `${peak.lag_samples >= 0 ? '+' : ''}${peak.lag_samples} 格（${peak.lag_seconds >= 0 ? '+' : ''}${peak.lag_seconds} 秒），r=${Number(peak.pearson_r).toFixed(4)}，${peak.pair_count} 对`;
}

function interferencePanel(check) {
  if (!check) return null;
  const original = el('div', {class:'interference-peak', 'data-testid':'interference-original'},
    el('h3', {}, '原峰（未剔除任何时刻）'),
    el('p', {class:'intro-copy'}, peakText(check.original_peak)),
    p('剔除前后共用同一峰判法：按 |r| 取大、12 位小数比较，再依次比配对数、绝对滞后、滞后值。', 'subtle'));
  const counter = check.counterexample;
  const body = counter
    ? el('div', {},
        el('p', {class:'feedback'}, `最少剔除 ${counter.removal_count} 个声级时刻即${counter.kind}；同剔除数下取时间最早的一组，风机读数全程保留。`),
        table([
          {key:'order', label:'剔除次序'},
          {key:'timestamp', label:'剔除的声级采样时刻'},
          {key:'slot', label:'网格槽位'},
          {key:'change', label:'峰变化'},
          {label:'剔除后峰位', render:() => peakText(counter.new_peak)},
        ], counter.removed_timestamps.map((stamp, index) => ({
          order:index + 1, timestamp:stamp, slot:counter.removed_slots[index], change:counter.kind,
        })), {caption:'抗干扰反例证据（同一组声级时刻）'}))
    : p(check.state === 'stable'
        ? `1 至 3 个声级时刻的组合已全部枚举（共 ${check.evaluated_combinations} 组），查全无反例，才报稳定。`
        : '原扫描没有可判读的相关峰，已标明「原扫描无峰」，不进行组合剔除。', 'empty');
  const basis = (check.sections || []).map(item => panel(item.title, p(item.text)));
  return el('section', {class:'panel interference', 'data-testid':'interference-panel'},
    h(2, `抗干扰复核 · ${INTERFERENCE_STATE[check.state] || check.state_label || ''}`),
    p(check.headline, 'intro-copy'),
    original,
    metrics(check.metrics || []),
    body,
    ...basis);
}


export async function results(route, refresh) {
  const {spec,runs,datasets} = workspace;
  const id = route.id || runs[0]?.id;
  if (!id) return panel(spec.pages[3],empty('还没有可查看的运行结果。','#run','前往运行'));
  const job = await workspace.getRun(id);
  const source = datasets.find(item => item.id === job.dataset_id);
  const message = feedback();
  const finished = item => item.status === 'complete' || item.status === 'failed';
  if (!finished(job)) {
    const original = location.hash;
    const status = el('p',{role:'status'},'后台正在计算，完成后会自动展示。');
    poll(()=>workspace.getRun(id),finished,()=>location.hash===original)
      .then(result=>{if (result && status.isConnected && location.hash===original) refresh();
        else if(status.isConnected) status.textContent='仍在执行，请点击刷新检查。';})
      .catch(error=>{if(status.isConnected) status.textContent=error.message;});
    return panel(job.name,pill(STATUS[job.status],job.status),status,button('刷新',{class:'quiet',onClick:refresh}));
  }
  const download = button('下载结果 CSV',{class:'quiet','data-testid':'export',onClick:event=>
    handler(download,message,()=>workspace.download(`/runs/${job.id}/export`,`${spec.short_name}-${job.id}.csv`))(event)});
  const history = el('label',{class:'field run-picker',for:'run-picker'},el('span',{},'切换运行记录'),
    el('select',{id:'run-picker',onChange:event=>{location.hash=`#results/${event.target.value}`;}},
      (runs.some(item=>item.id===job.id) ? runs : [job,...runs]).map(item=>el('option',{value:item.id,selected:item.id===job.id},item.name))));
  const result = job.result;
  const analysis = result ? el('section',{class:'analysis','data-testid':'analysis-complete'},
    el('div',{class:'result-head'},p(job.name,'eyeline'),h(2,result.headline),p(result.note,'intro-copy'),
      el('div',{class:'actions'},pill(STATUS[job.status],job.status),download,message)),
    metrics(result.metrics),
    result.charts.map(plot),
    interferencePanel(result.interference),
    (result.sections || []).map(item=>panel(item.title,p(item.text))),
    result.tables.map(item=>panel(item.title,table(item.columns,item.rows,{caption:item.title}))))
    : panel(job.name,pill(STATUS[job.status],job.status),p(job.error,'feedback'),link('返回调整参数',`#run/${job.dataset_id}`,{class:'quiet'}));
  const inputs = spec.params.map(param=>el('div',{},el('dt',{},param.label),el('dd',{},value(job.params[param.key]))));
  return el('div',{},el('div',{class:'two-col'},el('div',{class:'wide-panel'},analysis),
    el('aside',{class:'sidebar-panel'},panel('运行来源',history,
      source && link(source.name,`#inputs/${source.id}`),p(date(job.finished_at),'subtle'),
      el('dl',{class:'dictionary'},inputs)),
    result && panel(spec.decision, pill(REVIEW[job.review],job.review),
      link('打开复核记录',`#review/${job.id}`,{class:'quiet'})))));
}
