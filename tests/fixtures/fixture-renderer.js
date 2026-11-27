const editor=document.querySelector('textarea'),button=document.querySelector('button');
let attachments=0,messages=[];
const history=document.querySelector('#history');
for(let i=1;i<=80;i++){const p=document.createElement('p');p.textContent='Тестовая реплика '+i;history.appendChild(p);}
function state(){button.disabled=!editor.value&&!attachments;window.fixture.state({text:editor.value,attachments,messages,active:document.activeElement?.tagName,historyScrollTop:history.scrollTop});}
history.addEventListener('scroll',state);
editor.addEventListener('input',state);
editor.addEventListener('paste',e=>{
  if([...e.clipboardData.items].some(item=>item.type.startsWith('image/'))){
    e.preventDefault();attachments++;document.getElementById('attachments').textContent='Вложений: '+attachments;state();
  }
});
button.addEventListener('click',()=>{messages.push({text:editor.value,attachments});editor.value='';attachments=0;state();});
state();
setInterval(state,500);
