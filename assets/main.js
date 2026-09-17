document.addEventListener('DOMContentLoaded', () => {
    const timeElement = document.getElementById('time');
    
    function updateTime() {
        const now = new Date();
        timeElement.textContent = `当前时间：${now.toLocaleString('zh-CN')}`;
    }
    
    updateTime();
    setInterval(updateTime, 1000);
});