try{
 await import('./order_model.js');
 await import('./engine.js');
 await import('./assets.js');
 await import('./factory_model.js');
 await import('./scene.js');
 await import('./app.js');
 await import('./factory-ui.js');
 await import('./order-ui.js');
}catch(error){console.error(error);document.getElementById('scene-loading').hidden=true;const panel=document.getElementById('scene-error');panel.hidden=false;panel.textContent='공장 화면을 시작하지 못했습니다. 로컬 서버가 실행 중인지 확인하고 페이지를 다시 열어 주세요. 계속 실패하면 포함된 실행 파일과 서버 연결을 확인하세요.';}
