import './engine.js';
import './assets.js';
import './scene.js';
try{await import('./app.js');}
catch(error){console.error(error);document.getElementById('scene-loading').hidden=true;const panel=document.getElementById('scene-error');panel.hidden=false;panel.textContent='3D 화면을 시작하지 못했습니다. WebGL을 지원하는 브라우저에서 서버 주소로 열어 주세요.';}
