const editor=document.querySelector('textarea'),button=document.querySelector('button');
let attachments=0,messages=[];
function state(){button.disabled=!editor.value&&!attachments;window.fixture.state({text:editor.value,attachments,messages,active:document.activeElement?.tagName});}
editor.addEventListener('input',state);
editor.addEventListener('paste',e=>{
  if([...e.clipboardData.items].some(item=>item.type.startsWith('image/'))){
    e.preventDefault();attachments++;document.getElementById('attachments').textContent='Вложений: '+attachments;state();
  }
});
button.addEventListener('click',()=>{messages.push({text:editor.value,attachments});editor.value='';attachments=0;state();});
state();
setInterval(state,500);
