// auth.js - JWT 토큰 관리 및 인증

(function() {
  'use strict';

  // ============================================
  // 인증이 필요 없는 페이지 목록
  // ============================================
  const PUBLIC_PAGES = [
    '/login',
    '/signup',
    '/forgot-password',
    '/reset-password'
  ];

  // 현재 페이지가 공개 페이지인지 확인
  function isPublicPage() {
    const path = window.location.pathname;
    return PUBLIC_PAGES.some(publicPath => path.startsWith(publicPath));
  }

  // ============================================
  // 토큰 관리
  // ============================================
  
  function getToken() {
    return localStorage.getItem('token');
  }

  function setToken(token) {
    localStorage.setItem('token', token);
  }

  function removeToken() {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
  }

  // ============================================
  // 인증 확인
  // ============================================
  
  function checkAuth() {
    // 공개 페이지는 토큰 체크 안 함
    if (isPublicPage()) {
      console.log('공개 페이지 - 토큰 체크 건너뜀');
      return;
    }

    const token = getToken();
    
    if (!token) {
      console.log('토큰 없음 - 로그인 페이지로 이동');
      window.location.href = '/login';
      return;
    }

    // 토큰이 있으면 계속 진행
    console.log('토큰 확인됨');
  }

  // ============================================
  // API 요청 (토큰 자동 포함)
  // ============================================
  
  async function apiRequest(url, options = {}) {
    const token = getToken();
    
    const headers = {
      'Content-Type': 'application/json',
      ...options.headers
    };

    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const config = {
      ...options,
      headers
    };

    try {
      const response = await fetch(url, config);
      
      // 401 에러 (인증 실패) → 로그인 페이지로
      if (response.status === 401) {
        console.log('인증 실패 (401) - 로그인 페이지로 이동');
        removeToken();
        
        // 공개 페이지가 아닌 경우에만 리다이렉트
        if (!isPublicPage()) {
          window.location.href = '/login';
        }
        return null;
      }

      const data = await response.json();
      return data;
      
    } catch (error) {
      console.error('API 요청 실패:', error);
      throw error;
    }
  }

  // ============================================
  // 로그아웃
  // ============================================
  
  function logout() {
    removeToken();
    window.location.href = '/login';
  }

  // ============================================
  // 전역 객체로 노출
  // ============================================
  
  window.snaplogAuth = {
    getToken,
    setToken,
    removeToken,
    checkAuth,
    apiRequest,
    logout,
    isPublicPage
  };

  // ============================================
  // 페이지 로드 시 자동 인증 확인
  // ============================================
  
  // DOM 로드 완료 후 실행
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', checkAuth);
  } else {
    checkAuth();
  }

})();