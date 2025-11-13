// auth.js - 인증 관련 유틸리티

// 토큰 가져오기
function getToken() {
  return localStorage.getItem('snaplog_token');
}

// 사용자 정보 가져오기
function getUser() {
  const userStr = localStorage.getItem('snaplog_user');
  return userStr ? JSON.parse(userStr) : null;
}

// 로그아웃
function logout() {
  localStorage.removeItem('snaplog_token');
  localStorage.removeItem('snaplog_user');
  window.location.href = '/login';
}

// 인증 확인 (페이지 로드 시 자동 실행)
function checkAuth() {
  const token = getToken();
  
  // 로그인/회원가입 페이지는 체크하지 않음
  const currentPath = window.location.pathname;
  if (currentPath === '/login' || currentPath === '/signup') {
    return;
  }
  
  // 토큰이 없으면 로그인 페이지로
  if (!token) {
    window.location.href = '/login';
    return;
  }
  
  console.log('✅ 인증됨:', getUser());
}

// API 요청 헬퍼 (자동으로 토큰 포함)
async function apiRequest(url, options = {}) {
  const token = getToken();
  
  const defaultOptions = {
    headers: {
      'Content-Type': 'application/json',
      ...(token && { 'Authorization': `Bearer ${token}` })
    }
  };
  
  const mergedOptions = {
    ...defaultOptions,
    ...options,
    headers: {
      ...defaultOptions.headers,
      ...options.headers
    }
  };
  
  try {
    const response = await fetch(url, mergedOptions);
    const data = await response.json();
    
    // 인증 실패 시 로그인 페이지로
    if (response.status === 401) {
      logout();
      return null;
    }
    
    return data;
  } catch (error) {
    console.error('API 요청 실패:', error);
    throw error;
  }
}

// 페이지 로드 시 인증 체크
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', checkAuth);
} else {
  checkAuth();
}

// 전역으로 export
window.snaplogAuth = {
  getToken,
  getUser,
  logout,
  checkAuth,
  apiRequest
};