#################################
#Extended iterated Kalman filter#
#################################

#lansdell. Feb 9th 2016

import numpy as np
import cv2 

from distmesh_dyn import DistMesh
from renderer import Renderer

import pdb

class KFState:
	def __init__(self, distmesh, im, flow, cuda, eps_F = 1, eps_H = 1e-3, vel = None):
		#Set up initial geometry parameters and covariance matrices
		self._ver = np.array(distmesh.p, np.float32)
		#self._vel = np.zeros(self._ver.shape, np.float32)
		if vel is None:
			self._vel = np.zeros(self._ver.shape, np.float32)
			#For testing we'll give some initial velocity
			#self._vel = -3*np.ones(self._ver.shape, np.float32)
		else:
			self._vel = vel.reshape(self._ver.shape)

		#Set up initial guess for texture
		self.tex = im
		self.nx = im.shape[0]
		self.ny = im.shape[1]
		self.M = self.nx*self.ny

		#Number of observations
		self.NZ = self.M
		self.eps_F = eps_F
		self.eps_H = eps_H

		#Fixed quantities
		#Coordinates relative to texture. Stays constant throughout video
		self.N = distmesh.size()
		self.u = self._ver
		#Orientation of simplices
		self.tri = distmesh.t
		self.NT = self.tri.shape[0]
		#The SciPy documentation claims the edges are provided in an order that orients
		#them counter-clockwise, though this doesn't appear to be the case...
		#So we should keep the orientations here. Still not sure if the magnitude
		#of the cross product is useful or not. Probably not
		a = self._ver[self.tri[:,1],:] - self._ver[self.tri[:,0],:]
		b = self._ver[self.tri[:,2],:] - self._ver[self.tri[:,0],:]
		self.ori = np.sign(np.cross(a,b))

		#Form state vector
		self.X = np.vstack((self._ver.reshape((-1,1)), self._vel.reshape((-1,1))))
		e = np.eye(2*self.N)
		z = np.zeros((2*self.N,2*self.N))
		self.F = np.bmat([[e, e], [z, e]])
		self.Weps = eps_F * np.bmat([[e/4, e/2], [e/2, e]])
		self.W = np.eye(self._vel.shape[0]*4)

		#Renderer
		self.renderer = Renderer(distmesh, self._vel, flow, self.nx, im, cuda)

	def get_flow(self):
		return self.renderer.get_flow()

	def size(self):
		return self.X.shape[0]

	def refresh(self):
		self.renderer.update_vertex_buffer(self.vertices(), self.velocities())

	def render(self):
		self.renderer.on_draw(None)

	def update(self, y_im, y_flow):
		Hz = self._jacobian(y_im, y_flow)
		HTH = self._hessian(y_im, y_flow)
		return (Hz, HTH)

	def _jacobian(self, y_im, y_flow, deltaX = 2):
		Hz = np.zeros((self.size(),1))
		self.refresh() 
		self.render()
		#Set reference image to unperturbed images
		self.renderer.initjacobian(y_im, y_flow)
		for idx in range(self.size()):
			self.X[idx,0] += deltaX
			self.refresh()
			self.render()
			hz = self.renderer.jz()
			Hz[idx,0] = hz/deltaX
			self.X[idx,0] -= deltaX

			self.X[idx,0] -= deltaX
			self.refresh()
			self.render()
			hz = self.renderer.jz()
			Hz[idx,0] -= hz/deltaX
			self.X[idx,0] += deltaX
			Hz[idx,0] = Hz[idx,0]/2
		self.refresh() 
		self.render()
		return Hz

	def _hessian(self, y_im, y_flow, deltaX = 2):
		HTH = np.zeros((self.size(),self.size()))
		self.refresh() 
		self.render()
		#Set reference image to unperturbed images
		self.renderer.initjacobian(y_im, y_flow)
		#Very inefficient... for now 
		for i in range(self.size()):
			for j in range(i, self.size()):
				hij = self.renderer.j(self, deltaX, i, j)
				HTH[i,j] = hij/deltaX/deltaX
				#Fill in the other diagonal
				HTH[j,i] = HTH[i,j]
		self.refresh() 
		self.render()
		return HTH

	def vertices(self):
		return self.X[0:(2*self.N)].reshape((-1,2))

	def velocities(self):
		return self.X[(2*self.N):].reshape((-1,2))

class KalmanFilter:
	def __init__(self, distmesh, im, flow, cuda, vel = None):
		self.distmesh = distmesh
		self.N = distmesh.size()
		print 'Creating filter with ' + str(self.N) + ' nodes'
		self.state = KFState(distmesh, im, flow, cuda, vel=vel)

	def compute(self, y_im, y_flow, imageoutput = None):
		self.state.renderer.update_frame(y_im, y_flow)
		self.predict()
		self.update(y_im, y_flow)
		print self.state.X 
		#Save state of each frame
		if imageoutput is not None:
			self.state.renderer.screenshot(saveall=True, basename = imageoutput)
		#Compute error between predicted image and actual frames
		return self.error(y_im, y_flow)

	def predict(self):
		print 'Predicting'
		#import rpdb2 
		#rpdb2.start_embedded_debugger("asdf")

		X = self.state.X 
		F = self.state.F 
		Weps = self.state.Weps
		W = self.state.W 

		#Prediction equations 
		self.state.X = np.dot(F,X)
		self.state.W = np.dot(F, np.dot(W,F.T)) + Weps 
		#print np.sum(self.state.velocities())

	def size(self):
		#State space size
		return self.N*4

	def update(self, y_im, y_flow):
		#import rpdb2 
		#rpdb2.start_embedded_debugger("asdf")
		print 'Updating'
		X = self.state.X
		W = self.state.W
		eps_H = self.state.eps_H
		(Hz, HTH) = self.state.update(y_im, y_flow)
		np.set_printoptions(threshold = 'nan', linewidth = 150, precision = 1)
		invW = np.linalg.inv(W) + HTH/eps_H
		W = np.linalg.inv(invW)
		self.state.X = X + np.dot(W,Hz)/eps_H
		self.state.W = W 

	def error(self, y_im, y_flow):
		#Compute error of current state and current images and flow data
		return self.state.renderer.error(self.state, y_im, y_flow)

class IteratedKalmanFilter(KalmanFilter):
	def __init__(self, distmesh, im, flow, cuda):
		KalmanFilter.__init__(self, distmesh, im, flow, cuda)
		self.nI = 100

	def update(self, y_im, y_flow = None):
		#import rpdb2 
		#rpdb2.start_embedded_debugger("asdf")
		print 'Updating'
		X = self.state.X
		W = self.state.W
		eps_H = self.state.eps_H
		for i in range(self.nI):
			(Kres, HTH) = self.state.update(y_im, y_flow)
			self.state.X = X + W*Kres/eps_H
		invW = np.linalg.inv(W) + HTH/eps_H 
		self.state.W = np.linalg.inv(invW)

class KFStateMorph(KFState):
	def __init__(self, distmesh, im, flow, cuda, eps_Q = 1, eps_R = 1e-3):
		#Set up initial geometry parameters and covariance matrices
		self._ver = np.array(distmesh.p, np.float32)
		#Morph basis connecting mesh points to morph bases
		#T and K have the property that T*K = X (positions of nodes)
		self._generate_morph_basis(distmesh)

		self._vel = np.zeros(self.K.shape, np.float32)
		#For testing we'll give some initial velocity
		self._vel = np.ones(self.K.shape, np.float32)

		#Set up initial guess for texture
		self.tex = im
		self.nx = im.shape[0]
		self.ny = im.shape[1]
		self.M = self.nx*self.ny

		#Number of 'observations'
		self.NZ = self.M
		self.eps_Q = eps_Q
		self.eps_R = eps_R

		#Fixed quantities
		#Coordinates relative to texture. Stays constant throughout video
		self.N = self.K.shape[0]
		self.u = self._ver
		#Orientation of simplices
		self.tri = distmesh.t
		self.NT = self.tri.shape[0]
		#The SciPy documentation claims the edges are provided in an order that orients
		#them counter-clockwise, though this doesn't appear to be the case...
		#So we should keep the orientations here. Still not sure if the magnitude
		#of the cross product is useful or not. Probably not
		a = self._ver[self.tri[:,1],:] - self._ver[self.tri[:,0],:]
		b = self._ver[self.tri[:,2],:] - self._ver[self.tri[:,0],:]
		self.ori = np.sign(np.cross(a,b))

		#Form state vector
		self.X = np.vstack((self.K.reshape((-1,1)), self._vel.reshape((-1,1))))
		self.V = self.velocities().reshape((-1,1))
		e = np.eye(2*self.N)
		z = np.zeros((2*self.N,2*self.N))
		self.F = np.bmat([[e, e], [z, e]])
		self.Q = eps_Q * np.bmat([[e/4, e/2], [e/2, e]])
		self.R = eps_R * np.ones((self.NZ,self.NZ))
		self.P = np.eye(self._vel.shape[0]*4)

		#Renderer
		self.renderer = Renderer(distmesh, self.V, flow, self.nx, im, self.eps_R, cuda)

	def _generate_morph_basis(self, distmesh):
		#import rpdb2 
		#rpdb2.start_embedded_debugger("asdf")
		self.T = np.ones((distmesh.p.shape[0], 3))
		#K here is just translation of points
		self.K = np.mean(distmesh.p, axis = 0)
		self.T[:,0:2] = distmesh.p - self.K 

	def vertices(self):
		ver = self.X[0:2].reshape((-1,2))
		K = np.vstack((np.np.eye(2), ver))
		return np.dot(self.T,K)

	def velocities(self):
		vel = self.X[2:].reshape((-1,2))
		K = np.vstack((np.zeros((2,2)), vel))
		return np.dot(self.T,K)

class KalmanFilterMorph(KalmanFilter):
	def __init__(self, distmesh, im, flow, cuda):
		self.distmesh = distmesh
		self.N = distmesh.size()
		print 'Creating filter with ' + str(self.N) + ' nodes'
		self.state = KFStateMorph(distmesh, im, flow, cuda)

	def linearize_obs(self, z_tilde, y_im, deltaX = 2):
		H = np.zeros((self.state.M, self.size()))
		for idx in range(self.state.N*2):
			self.state.X[idx,0] += deltaX
			zp = self.observation(y_im)
			self.state.X[idx,0] -= deltaX
			H[:,idx] = (z_tilde - zp)/deltaX
		return H
