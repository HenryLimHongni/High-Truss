/*
 * MyVectorInt.cpp
 *
 *  Created on: 26 Oct, 2015
 *      Author: jhgan
 */

#include "MyVectorInt.h"

template<class T>
MyVectorInt<T>::MyVectorInt() {
	// TODO Auto-generated constructor stub
	this->elementList = NULL;
}

template<class T>
MyVectorInt<T>::~MyVectorInt() {
	// TODO Auto-generated destructor stub
}

template<class T>
T* MyVectorInt<T>::get_list() const {
	if (this->elementList == NULL) {
		return NULL;
	}
	return this->elementList + 2;
}
template<class T>
unsigned int MyVectorInt<T>::size() const {
	if (this->elementList == NULL) {
		return 0;
	}
	return this->elementList[0];
}

template<class T>
unsigned int MyVectorInt<T>::capacity() const {
	if (this->elementList == NULL) {
		return 0;
	}
	return this->elementList[1];
}

template<class T>
void MyVectorInt<T>::push_back(T _val) {
	if (this->elementList != NULL
			&& this->elementList[0] < this->elementList[1]) {
		this->elementList[elementList[0] + 2] = _val;
		elementList[0]++;
	} else {
		// Need to enlarge the length of the array.
		this->enlarge();
		this->elementList[elementList[0] + 2] = _val;
		elementList[0]++;
	}

//	if (this->elementNum < this->length) {
//		this->elementList[elementNum] = _val;
//		elementNum++;
//	} else {
//		// Need to enlarge the length of the array.
//		this->enlarge();
//		this->elementList[elementNum] = _val;
//		elementNum++;
//	}
}

template<class T>
void MyVectorInt<T>::pop_back() {
	if (this->elementList != NULL && this->elementList[0] > 0) {
		elementList[0]--;
	} else {
		printf("Error in MyVectorInt::pop_back: Out of boundary!\n");
		exit(0);
	}

//	if (this->elementNum > 0) {
//		elementNum--;
//	} else {
//		printf("Error in MyVector<T>::pop_back: Out of boundary!\n");
//		exit(0);
//	}
}

template<class T>
void MyVectorInt<T>::reserve(unsigned int len) {
	if (this->elementList == NULL) {
		this->elementList = (T*) malloc(sizeof(T) * (len + 2));
		if (this->elementList == NULL) {
			printf(
					"Error in MyVectorInt::reserve: The allocation is failed!\n");
			exit(0);
		}

		this->elementList[0] = 0;
		this->elementList[1] = len;

	} else if (len > (unsigned int) this->elementList[1]) {
		T* temp = this->elementList;
		int elementNum = this->elementList[0];
		this->elementList = (T*) realloc(temp, sizeof(T) * (len + 2));
		if (this->elementList == NULL) {
			printf(
					"Error in MyVectorInt::reserve: The reallocation is failed!\n");
			exit(0);
		}
		this->elementList[0] = elementNum;
		this->elementList[1] = len;
	}

//	if (len > this->length) {
//		T* temp = this->elementList;
////		this->elementList = new T[len];
//
//		this->elementList = (T*) realloc(temp, sizeof(T) * len);
//		if (this->elementList == NULL) {
//			printf("Error in reserve: The reallocation is failed!\n");
//			exit(0);
//		}
//		this->length = len;
//	}
}

template<class T>
void MyVectorInt<T>::resize(unsigned int targetSize, T _val) {
	if (this->elementList == NULL) {
		this->reserve(targetSize);
		unsigned int temp = this->elementList[0];
		for (unsigned int i = temp; i < targetSize; ++i) {
			this->elementList[i + 2] = _val;
			elementList[0]++;
		}
	} else if ((unsigned int) this->elementList[0] >= targetSize) {
		// Shrink to targetSize.
		this->elementList[0] = targetSize;
	} else {
		if ((unsigned int) this->elementList[1] < targetSize) {
			// Need to enlarge the length of the array.
			this->reserve(targetSize);
		}
		unsigned int temp = this->elementList[0];
		for (unsigned int i = temp; i < targetSize; ++i) {
			this->elementList[i + 2] = _val;
			elementList[0]++;
		}
	}

//	if (this->elementNum >= targetSize) {
//		// Shrink to targetSize.
//		this->elementNum = targetSize;
//	} else {
//		if (this->length < targetSize) {
//			// Need to enlarge the length of the array.
//			this->reserve(targetSize);
//		}
//		unsigned int temp = this->elementNum;
//		for (unsigned int i = temp; i < targetSize; ++i) {
//			this->elementList[i] = _val;
//			elementNum++;
//		}
//	}
}

template<class T>
void MyVectorInt<T>::resize(unsigned int targetSize) {
	if (this->elementList == NULL) {
		this->reserve(targetSize);
	} else if ((unsigned int) this->elementList[0] >= targetSize) {
		// Shrink to targetSize.
		this->elementList[0] = targetSize;
	} else {
		if ((unsigned int) this->elementList[1] < targetSize) {
			// Need to enlarge the length of the array.
			this->reserve(targetSize);
		}
	}
//	if (this->elementNum >= targetSize) {
//		// Shrink to targetSize.
//		this->elementNum = targetSize;
//	} else {
//		if (this->length < targetSize) {
//			// Need to enlarge the length of the array.
//			this->reserve(targetSize);
//		}
//		this->elementNum = targetSize;
//	}
}

template<class T>
void MyVectorInt<T>::shrink_to_fit() {
	if (this->elementList != NULL && this->elementList[0] == 0) {
		this->release_space();
		return;
	}

	if (this->elementList != NULL
			&& (unsigned int) this->elementList[0]
					< (unsigned int) this->elementList[1]) {
		T* temp = this->elementList;
		unsigned int elementNum = temp[0];

//		this->elementList = new T[this->elementNum];
		this->elementList = (T*) realloc(temp, sizeof(T) * (elementNum + 2));

		if (this->elementList == NULL) {
			// realloc is failed.
			printf(
					"Error in MyVectorInt::shrink_to_fit: the reallocation is fail!\n");
			exit(0);
		}

		this->elementList[0] = elementNum;
		this->elementList[1] = elementNum;
	}

//	if (this->elementNum < this->length) {
//		T* temp = this->elementList;
////		this->elementList = new T[this->elementNum];
//		this->elementList = (T*) realloc(temp, sizeof(T) * this->elementNum);
//
//		if (this->elementList == NULL) {
//			// realloc is failed.
//			printf("Error in shrink_to_fit: the reallocation is fail!\n");
//			exit(0);
//		}
//
//		this->length = this->elementNum;
//	}
}

template<class T>
void MyVectorInt<T>::swap(MyVectorInt<T>& _list) {
	T* temp = this->elementList;
	this->elementList = _list.elementList;
	_list.elementList = temp;

//
//
//	unsigned int temp = this->elementNum;
//	this->elementNum = _list.elementNum;
//	_list.elementNum = temp;
//
//	temp = this->length;
//	this->length = _list.length;
//	_list.length = temp;
//
//	T* ptr = this->elementList;
//	this->elementList = _list.elementList;
//	_list.elementList = ptr;
}

template<class T>
void MyVectorInt<T>::clear() {
	if (this->elementList != NULL) {
		this->elementList[0] = 0;
	}
//	this->elementNum = 0;
}

template<class T>
void MyVectorInt<T>::release_space() {
//	this->elementNum = 0;
//	this->length = 0;
	if (this->elementList != NULL) {
//		delete[] (this->elementList);
		free(this->elementList);
		this->elementList = NULL;
	}
}

template<class T>
void MyVectorInt<T>::enlarge() {
	if (this->elementList == NULL) {
		this->elementList = (T*) malloc(sizeof(T) * 4);
		if (this->elementList == NULL) {
			printf(
					"Error in MyVectorInt::enlarge: The allocation is failed!\n");
			exit(0);
		}
		this->elementList[0] = 0;
		this->elementList[1] = 2;
	} else {
		this->reserve(this->elementList[1] * 2);
	}

//	if (this->length == 0) {
////		this->elementList = new T[2];
//		this->elementList = (T*) malloc(sizeof(T) * 2);
//		this->length = 2;
//	} else {
//		this->reserve(this->length * 2);
//	}
}

template class MyVectorInt<int> ;
template class MyVectorInt<unsigned int> ;

